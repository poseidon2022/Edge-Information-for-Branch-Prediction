import os
import re
import glob
import numpy as np
from collections import defaultdict

def parse_control_flow(cf_file):
    """Parse node features from control_flow_features.txt."""
    cf_data = {}  # instr_id: [in_loop, dist, loop_depth, cond_type, opcode_cat]
    branch_mapping = {}
    instr_text = {} # instruction id to text mapping
    dependencies = defaultdict(lambda: [set(), 0])  # instr_id: [deps, dist] //dist is the max distance to any of dep instructions
    writes = {}
    reads = defaultdict(set)  # instr_id: {read operands} //one instructions might have multiple read operands
    instr_id = 0
    current_instr_id = None
    
    with open(cf_file, 'r') as f:
        for line in f:
            line = line.strip()
            if not line or "Control-flow features for function" in line:
                continue
            if "Depends on:" in line:
                deps = re.findall(r"([^,:]+(?:\s*=.*)?)(?:,|$)", line.split("Depends on:")[1])
                for dep in deps:
                    dep = dep.strip()
                    dep_id = next((id for id, text in instr_text.items() if text.startswith(dep)), None)
                    if dep_id is not None:
                        dependencies[current_instr_id][0].add(dep_id)
                        dependencies[current_instr_id][1] = max(dependencies[current_instr_id][1], abs(current_instr_id - dep_id))
                continue
            

            # now identify if the instruction is a branch or a normal instruction inorder to comeup with the control flow information
            branch_match = re.match(r"BranchID:\s*(\d+)\s+(.+?): \[in_loop: (\d), dist_to_branch: (\d+)\]", line)
            if branch_match:
                branch_id, instr, in_loop, dist = branch_match.groups()
                in_loop, dist = int(in_loop), int(dist)
                depth = in_loop
                cond_type = 1 if "icmp" in instr else 0
                cf_data[instr_id] = [in_loop, dist, depth, cond_type, 2]
                branch_mapping[int(branch_id)] = instr_id
                instr_text[instr_id] = instr
            else:
                instr_match = re.match(r"(.+?): \[in_loop: (\d), dist_to_branch: (\d+)\]", line)
                if instr_match:
                    instr, in_loop, dist = instr_match.groups()
                    in_loop, dist = int(in_loop), int(dist)
                    depth = in_loop
                    opcode_cat = 0 if any(op in instr for op in ["add", "sub", "mul"]) else 1 if "store" in instr or "load" in instr else 2 if "br" in instr else 3
                    cf_data[instr_id] = [in_loop, dist, depth, 0, opcode_cat]
                    instr_text[instr_id] = instr
            parts = instr_text[instr_id].split()
            for i, part in enumerate(parts):
                if part == '=' and i > 0:
                    write_match = re.search(r"%(\d+)", parts[i - 1])
                    if write_match:
                        writes[instr_id] = int(write_match.group(1))
                elif part == 'store' and i + 2 < len(parts):
                    write_match = re.search(r"%(\d+)", parts[i + 2])  # First operand after 'store i32'
                    if write_match:
                        writes[instr_id] = int(write_match.group(1))
                elif "load" in part or any(op in part for op in ["add", "sub", "mul", "icmp"]):
                    for j in range(i + 1, len(parts)):
                        read_match = re.search(r"%(\d+)", parts[j])
                        if read_match:
                            reads[instr_id].add(int(read_match.group(1)))
            current_instr_id = instr_id
            instr_id += 1
    return cf_data, branch_mapping, instr_text, dependencies, writes, reads

def parse_branch_history(bh_file):
    """Parse branch_history.log for edge-relevant dynamic features."""
    branch_outcomes = defaultdict(list)
    try:
        with open(bh_file, 'r') as f:
            next(f)
            for line in f:
                branch_id, taken = map(int, line.strip().split(','))
                branch_outcomes[branch_id].append(taken)
    except Exception as e:
        print(f"Error parsing {bh_file}: {e}")
        return {}, {}

    history_features = {}
    all_corrs = {}
    for branch_id, outcomes in branch_outcomes.items():
        n = len(outcomes)
        taken_prob = sum(outcomes) / n if n > 0 else 0.0
        geo = [sum(outcomes[-l:]) / l if n >= l else taken_prob for l in [2, 4, 8]]
        history_features[branch_id] = [taken_prob] + geo

    branch_ids = list(branch_outcomes.keys())
    for i in range(len(branch_ids)):
        for j in range(i + 1, len(branch_ids)):
            b1, b2 = branch_ids[i], branch_ids[j]
            outcomes1, outcomes2 = branch_outcomes[b1], branch_outcomes[b2]
            min_len = min(len(outcomes1), len(outcomes2))
            if min_len > 1:
                corr = float(np.corrcoef(outcomes1[:min_len], outcomes2[:min_len])[0, 1])
                if not np.isnan(corr):
                    all_corrs[(b1, b2)] = corr
                    all_corrs[(b2, b1)] = corr

    return history_features, all_corrs

def get_successors(instr):
    if "br i1" in instr:
        parts = instr.split("label")
        return [int(re.search(r"%(\d+)", p).group(1)) for p in parts[1:] if re.search(r"%(\d+)", p)]
    elif "br label" in instr:
        match = re.search(r"%(\d+)", instr)
        return [int(match.group(1))] if match else []
    return []

def build_edge_features(cf_data, branch_mapping, instr_text, dependencies, writes, reads, bh_data, all_corrs):
    """Build true edge features, all 8D, with dist_to_branch"""
    edge_features = {}
    
    for src_id in cf_data:
        src_instr = instr_text[src_id]
        
        if src_id in branch_mapping.values():  # Branch edges
            branch_id = [k for k, v in branch_mapping.items() if v == src_id][0]
            history = bh_data.get(branch_id, [0.0, 0.0, 0.0, 0.0])
            max_corr = max([abs(all_corrs.get((branch_id, other_id), 0.0)) for other_id in branch_mapping.keys() if other_id != branch_id], default=0.0)
            successors = get_successors(src_instr)
            if len(successors) == 2:  # Conditional branch
                for i, succ in enumerate(successors):
                    tgt = f"label %{succ}"
                    taken_prob = history[0] if i == 0 else 1.0 - history[0]
                    geo = [h if i == 0 else 1.0 - h for h in history[1:]]
                    src_in_loop = cf_data[src_id][0]
                    dist_to_branch = cf_data[src_id][1]  # From source
                    tgt_id = next((id for id, instr in instr_text.items() if f"label %{succ}" in instr), None)
                    tgt_in_loop = cf_data[tgt_id][0] if tgt_id else 0
                    edge_features[(src_instr, tgt)] = [dist_to_branch, taken_prob, *geo, src_in_loop, tgt_in_loop, 0, max_corr]
            elif len(successors) == 1:  # Unconditional branch
                tgt = f"label %{successors[0]}"
                tgt_id = next((id for id, instr in instr_text.items() if f"label %{successors[0]}" in instr), None)
                tgt_in_loop = cf_data[tgt_id][0] if tgt_id else 0
                dist_to_branch = cf_data[src_id][1]
                edge_features[(src_instr, tgt)] = [dist_to_branch, 1.0, 0.0, 0.0, 0.0, cf_data[src_id][0], tgt_in_loop, 0, 0.0]
        elif src_id + 1 in cf_data:  # Sequential edge
            tgt_instr = instr_text[src_id + 1]
            dist_to_branch = cf_data[src_id][1]
            edge_features[(src_instr, tgt_instr)] = [dist_to_branch, 1.0, 0.0, 0.0, 0.0, cf_data[src_id][0], cf_data[src_id + 1][0], 0, 0.0]
        
        # Dependency edges, overriding issue here!
        for dep_id in dependencies[src_id][0]:
            dep_instr = instr_text[dep_id]
            dep_type = 0
            write_val = writes.get(dep_id)
            read_vals = reads.get(src_id, set())
            if write_val is not None and read_vals and write_val in read_vals:
                dep_type = 1  # RAW
            read_vals_dep = reads.get(dep_id, set())
            src_write = writes.get(src_id)
            if src_write is not None and read_vals_dep and src_write in read_vals_dep:
                dep_type = 2  # WAR
            if dep_type:
                dist_to_branch = cf_data[dep_id][1]  # From dep_id (source of dependency edge)
                edge_features[(dep_instr, src_instr)] = [dist_to_branch, 0.0, 0.0, 0.0, 0.0, cf_data[dep_id][0], cf_data[src_id][0], dep_type, 0.0]

    return edge_features

def merge_features_for_corpus(ll_dir="dsa/dsa/llvm", cf_dir="control_flow_features", bh_dir="branch_history_logs", output_file="edge_features.txt"):
    corpus_data = {}
    
    ll_files = glob.glob(f"{ll_dir}/*.ll")
    total_files = len(ll_files)
    
    with open(output_file, 'w') as f:
        f.write(f"Found {total_files} LLVM IR files to process\n")
        
        for i, ll_file in enumerate(ll_files):
            base_name = os.path.basename(ll_file).replace('.ll', '')
            cf_file = f"{cf_dir}/{base_name}_control_flow_features.txt"
            bh_file = f"{bh_dir}/{base_name}_branch_history.log"
            
            progress = i + 1
            f.write(f"Processing {progress}/{total_files}: {base_name}\n")
            
            if not os.path.exists(cf_file) or not os.path.exists(bh_file):
                f.write(f"Warning: Missing files for {base_name}, skipping\n")
                continue
            
            cf_data, branch_mapping, instr_text, dependencies, writes, reads = parse_control_flow(cf_file)
            bh_data, all_corrs = parse_branch_history(bh_file)
            edge_features = build_edge_features(cf_data, branch_mapping, instr_text, dependencies, writes, reads, bh_data, all_corrs)
            
            corpus_data[base_name] = {"edge_features": edge_features, "branch_mapping": branch_mapping}
        
        for program_name, data in corpus_data.items():
            f.write(f"\nProgram: {program_name}\n")
            for (src, tgt), feat in data["edge_features"].items():
                f.write(f"  Edge \"{src}\" -> \"{tgt}\": {feat}\n")
    
    with open(output_file, 'r') as f:
        print(f.read())
    
    return corpus_data

corpus_data = merge_features_for_corpus()
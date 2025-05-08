import os
import re
import glob
import numpy as np
from collections import defaultdict

def parse_control_flow(cf_file):
    """Parse node features from control_flow_features.txt."""
    cf_data = {}  # instr_id: [in_loop, dist, loop_depth, cond_type, opcode_cat]
    branch_mapping = {}
    instr_text = {}  # instr_id: text
    dependencies = defaultdict(lambda: [set(), 0])  # instr_id: [deps, max_dist]
    writes = {}
    reads = defaultdict(set)  # instr_id: {read operands}
    func_map = {}
    func_entries = {}
    instr_order = defaultdict(list)  # func_name: [instr_id, ...]
    current_instr_id = None
    current_func = None
    first_instr_id = None
    
    with open(cf_file, 'r') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            if line.startswith("Control-flow features for function:"):
                current_func = line.split(":")[1].strip()
                first_instr_id = None
                continue
            if not current_func:
                continue
            
            if "Depends on:" in line:
                deps = re.findall(r"([^,:]+(?:\s*=.*)?)(?:,|$)", line.split("Depends on:")[1])
                for dep in deps:
                    dep = dep.strip()
                    dep_id = next((id for id, text in instr_text.items() if text.startswith(dep) and id.startswith(f"{current_func}_")), None)
                    if dep_id:
                        dependencies[current_instr_id][0].add(dep_id)
                        dependencies[current_instr_id][1] = max(dependencies[current_instr_id][1], 1)
                continue
            
            # Parse branch or normal instruction
            branch_match = re.match(r"BranchID:\s*(\d+)\s+(.+?): \[in_loop: (\d), dist_to_branch: (\d+)\]", line)
            if branch_match:
                branch_id, instr, in_loop, dist = branch_match.groups()
                in_loop, dist = int(in_loop), int(dist)
                depth = in_loop
                cond_type = 1 if "icmp" in instr else 0
                if "ret " in instr:
                    instr_id = f"{current_func}_%ret"
                else:
                    match = re.search(r'%(\d+)', instr)
                    instr_id = f"{current_func}_%{match.group(1)}" if match else f"{current_func}_%branch_{branch_id}"
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
                    if "ret " in instr:
                        instr_id = f"{current_func}_%ret"
                    else:
                        match = re.search(r'%(\d+)', instr)
                        instr_id = f"{current_func}_%{match.group(1)}" if match else f"{current_func}_%call" if "call " in instr else f"{current_func}_%instr"
                    cf_data[instr_id] = [in_loop, dist, depth, 0, opcode_cat]
                    instr_text[instr_id] = instr
            
            # Set first instruction as entry point
            if first_instr_id is None:
                first_instr_id = instr_id
                func_entries[current_func] = instr_id
            
            # Update func_map and instr_order
            func_map[instr_id] = current_func
            instr_order[current_func].append(instr_id)
            
            # Parse writes and reads
            parts = instr_text[instr_id].split()
            for i, part in enumerate(parts):
                if part == '=' and i > 0:
                    write_match = re.search(r"%(\d+)", parts[i - 1])
                    if write_match:
                        writes[instr_id] = f"{current_func}_%{write_match.group(1)}"
                elif part == 'store' and i + 2 < len(parts):
                    write_match = re.search(r"%(\d+)", parts[i + 2])
                    if write_match:
                        writes[instr_id] = f"{current_func}_%{write_match.group(1)}"
                elif any(op in part for op in ["load", "add", "sub", "mul", "icmp"]):
                    for j in range(i + 1, len(parts)):
                        read_match = re.search(r"%(\d+)", parts[j])
                        if read_match:
                            reads[instr_id].add(f"{current_func}_%{read_match.group(1)}")
                if 'call ' in part and '=' in parts:
                    writes[instr_id] = instr_id
                if part.startswith('ret '):
                    write_match = re.search(r"%(\d+)", parts[i + 1]) if i + 1 < len(parts) else None
                    if write_match:
                        writes[instr_id] = f"{current_func}_%{write_match.group(1)}"
            
            current_instr_id = instr_id
    
    return cf_data, branch_mapping, instr_text, dependencies, writes, reads, func_map, func_entries, instr_order

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

def build_edge_features(cf_data, branch_mapping, instr_text, dependencies, writes, reads, bh_data, all_corrs, max_dist=100, func_map=None, func_entries=None, instr_order=None):
    edge_features = {}
    
    for src_id in cf_data:
        src_instr = instr_text[src_id]
        src_func = func_map.get(src_id, None) if func_map else None
        src_in_loop = cf_data[src_id][0]
        dist_to_branch = cf_data[src_id][1]
        
        # Branch edges
        if src_id in branch_mapping.values():
            branch_id = [k for k, v in branch_mapping.items() if v == src_id][0]
            history = bh_data.get(branch_id, [0.0, 0.0, 0.0, 0.0])
            successors = get_successors(src_instr)
            if len(successors) == 2:
                for i, succ in enumerate(successors):
                    tgt = f"label %{succ}"
                    geo = [h if i == 0 else 1.0 - h for h in history[1:]]
                    tgt_id = next((id for id, instr in instr_text.items() if f"label %{succ}" in instr), None)
                    if tgt_id:
                        tgt_in_loop = cf_data[tgt_id][0]
                        edge_type = 1 if i == 0 else 2  # Taken, not-taken
                        edge_features[(src_instr, instr_text[tgt_id])] = [dist_to_branch, *geo, src_in_loop, tgt_in_loop, 0, edge_type]
            elif len(successors) == 1:
                tgt = f"label %{successors[0]}"
                tgt_id = next((id for id, instr in instr_text.items() if f"label %{successors[0]}" in instr), None)
                if tgt_id:
                    tgt_in_loop = cf_data[tgt_id][0]
                    edge_type = 3  # Unconditional
                    edge_features[(src_instr, instr_text[tgt_id])] = [dist_to_branch, 0.0, 0.0, 0.0, src_in_loop, tgt_in_loop, 0, edge_type]
        
        # Call edges
        elif 'call ' in src_instr and '@' in src_instr:
            callee_name = src_instr.split('@')[1].split('(')[0]
            if callee_name in func_entries:
                tgt_id = func_entries[callee_name]
                tgt_instr = instr_text[tgt_id]
                tgt_in_loop = cf_data[tgt_id][0] if tgt_id in cf_data else 0
                edge_type = 7  # Call
                edge_features[(src_instr, tgt_instr)] = [dist_to_branch, 0.0, 0.0, 0.0, src_in_loop, tgt_in_loop, 0, edge_type]
            
            # Sequential edge after call
            if src_func and src_id in instr_order[src_func]:
                src_idx = instr_order[src_func].index(src_id)
                if src_idx + 1 < len(instr_order[src_func]):
                    tgt_id = instr_order[src_func][src_idx + 1]
                    tgt_instr = instr_text[tgt_id]
                    tgt_in_loop = cf_data[tgt_id][0]
                    dep_type = 0
                    write_val = writes.get(src_id)
                    read_vals = reads.get(tgt_id, set())
                    if write_val is not None and read_vals and write_val in read_vals:
                        dep_type = 1  # RAW
                    edge_type = 0 if dep_type == 0 else 4  # Sequential or RAW
                    edge_features[(src_instr, tgt_instr)] = [dist_to_branch, 0.0, 0.0, 0.0, src_in_loop, tgt_in_loop, dep_type, edge_type]
        
        # Sequential edges (non-call)
        elif src_func and src_id in instr_order[src_func]:
            src_idx = instr_order[src_func].index(src_id)
            if src_idx + 1 < len(instr_order[src_func]):
                tgt_id = instr_order[src_func][src_idx + 1]
                tgt_instr = instr_text[tgt_id]
                tgt_in_loop = cf_data[tgt_id][0]
                dep_type = 0
                write_val = writes.get(src_id)
                read_vals = reads.get(tgt_id, set())
                if write_val is not None and read_vals and write_val in read_vals:
                    dep_type = 1  # Intra-RAW
                read_vals_tgt = reads.get(src_id, set())
                write_val_tgt = writes.get(tgt_id)
                if write_val_tgt is not None and read_vals_tgt and write_val_tgt in read_vals_tgt:
                    dep_type = 2  # Intra-WAR
                edge_type = 0 if dep_type == 0 else (4 if dep_type == 1 else 5)
                edge_features[(src_instr, tgt_instr)] = [dist_to_branch, 0.0, 0.0, 0.0, src_in_loop, tgt_in_loop, dep_type, edge_type]
        
        # Return edges
        if src_instr.startswith('ret ') and src_func:
            for caller_id, caller_instr in instr_text.items():
                if f'call ' in caller_instr and f'@{src_func}' in caller_instr:
                    caller_func = func_map.get(caller_id, None)
                    if caller_func != src_func and caller_id in instr_order[caller_func]:
                        caller_idx = instr_order[caller_func].index(caller_id)
                        if caller_idx + 1 < len(instr_order[caller_func]):
                            tgt_id = instr_order[caller_func][caller_idx + 1]
                            tgt_instr = instr_text[tgt_id]
                            tgt_in_loop = cf_data[tgt_id][0]
                            dep_type = 0
                            write_val = writes.get(src_id)
                            read_vals = reads.get(tgt_id, set())
                            if write_val is not None and read_vals and write_val in read_vals:
                                dep_type = 1  # RAW
                            edge_type = 8  # Return
                            edge_features[(src_instr, tgt_instr)] = [dist_to_branch, 0.0, 0.0, 0.0, src_in_loop, tgt_in_loop, dep_type, edge_type]
        
        # Dependency edges
        for dep_id in dependencies[src_id][0]:
            dep_func = func_map.get(dep_id, None) if func_map else None
            if src_func and dep_func and src_func != dep_func:
                write_val = writes.get(dep_id)
                read_vals = reads.get(src_id, set())
                if write_val is None or not read_vals or write_val not in read_vals or not write_val.startswith('@'):
                    continue
                dep_type = 3  # Inter-RAW
                edge_type = 6  # Inter-RAW
            else:
                dep_type = 0
                write_val = writes.get(dep_id)
                read_vals = reads.get(src_id, set())
                if write_val is not None and read_vals and write_val in read_vals:
                    dep_type = 1  # Intra-RAW
                read_vals_dep = reads.get(dep_id, set())
                src_write = writes.get(src_id)
                if src_write is not None and read_vals_dep and src_write in read_vals_dep:
                    dep_type = 2  # Intra-WAR
                edge_type = 4 if dep_type == 1 else 5 if dep_type == 2 else 0
            if dep_type:
                dep_instr = instr_text[dep_id]
                dist_to_branch = cf_data[dep_id][1]
                src_in_loop_dep = cf_data[dep_id][0]
                tgt_in_loop = cf_data[src_id][0]
                edge_features[(dep_instr, src_instr)] = [dist_to_branch, 0.0, 0.0, 0.0, src_in_loop_dep, tgt_in_loop, dep_type, edge_type]

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
            
            # Parse control flow
            cf_data, branch_mapping, instr_text, dependencies, writes, reads, func_map, func_entries, instr_order = parse_control_flow(cf_file)
            bh_data, all_corrs = parse_branch_history(bh_file)
            
            # Build edge features
            edge_features = build_edge_features(
                cf_data, branch_mapping, instr_text, dependencies, writes, reads,
                bh_data, all_corrs, max_dist=100, func_map=func_map, func_entries=func_entries, instr_order=instr_order
            )
            
            # Store in corpus_data
            corpus_data[base_name] = {
                "edge_features": edge_features,
                "branch_mapping": branch_mapping,
                "cf_data": cf_data,
                "instr_text": instr_text,
                "dependencies": dependencies,
                "writes": writes,
                "reads": reads,
                "bh_data": bh_data,
                "all_corrs": all_corrs,
                "func_map": func_map,
                "func_entries": func_entries,
                "instr_order": instr_order
            }
        
        # Write edge features
        for program_name, data in corpus_data.items():
            f.write(f"\nProgram: {program_name}\n")
            for (src, tgt), feat in data["edge_features"].items():
                f.write(f"  Edge \"{src}\" -> \"{tgt}\": {feat}\n")
    
    with open(output_file, 'r') as f:
        print(f.read())
    
    return corpus_data

corpus_data = merge_features_for_corpus()
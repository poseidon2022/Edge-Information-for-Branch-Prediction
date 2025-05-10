import os
import re
import glob
from collections import defaultdict

def parse_control_flow(cf_file):
    """Parse control_flow_features.txt to build structures matching generate_xfg."""
    cf_data = {}  # node_id: [in_loop, dist, loop_depth, cond_type, opcode_cat]
    instr_text = {}  # node_id: instruction text
    label_to_start = {}  # (func_name, label) -> node_id
    function_to_head_and_tail = {}  # func_name: [head_id, tail_id]
    function_scopes = {}  # func_name: {ssa_id: node_id}
    func_map = {}  # node_id: func_name
    instr_order = defaultdict(list)  # func_name: [node_id, ...]
    node_to_id = {}  # node_id: instr_id (e.g., main_%8)
    dependencies = defaultdict(set)  # node_id: {dep_node_id}
    current_function = None
    id_to_node = {}  # SSA identifiers for current function
    node_id = 0
    
    try:
        with open(cf_file, 'r') as f:
            lines = f.readlines()
            i = 0
            while i < len(lines):
                line = lines[i].strip()
                if not line:
                    i += 1
                    continue
                
                # Handle function definitions
                if line.startswith("Control-flow features for function:"):
                    current_function = line.split(":")[1].strip()
                    function_to_head_and_tail[current_function] = [node_id, None]
                    id_to_node = {}
                    function_scopes[current_function] = id_to_node
                    print(f"Function defined: {current_function}, head={node_id}")
                    i += 1
                    continue
                
                if not current_function:
                    i += 1
                    continue
                
                # Handle labels
                match_label = re.match(r"(\d+):", line)
                if match_label:
                    label = f"%{match_label.group(1)}"
                    label_to_start[(current_function, label)] = node_id + 1
                    print(f"Label {label} mapped to node {node_id + 1} in {current_function}")
                    i += 1
                    continue
                
                # Parse instruction and features
                match = re.match(r"(.+?)(?:\[in_loop: (\d), dist_to_branch: (\d+)\]|$)", line)
                if not match:
                    print(f"Warning: Skipping unparseable line: {line}")
                    i += 1
                    continue
                
                instr, in_loop, dist = match.groups()
                instr = instr.strip()
                in_loop, dist = int(in_loop or 0), int(dist or 0)
                depth = in_loop
                is_branch = "br i1" in instr
                opcode_cat = 0 if any(op in instr for op in ["add", "sub", "mul"]) else 1 if "store" in instr or "load" in instr else 2 if "br" in instr else 3
                cond_type = 1 if is_branch else 0
                
                cf_data[node_id] = [in_loop, dist, depth, cond_type, opcode_cat]
                instr_text[node_id] = instr
                func_map[node_id] = current_function
                instr_order[current_function].append(node_id)
                node_to_id[node_id] = f"{current_function}_%instr_{node_id}"
                
                if instr.startswith("ret"):
                    function_to_head_and_tail[current_function][1] = node_id
                    print(f"Function {current_function} tail set to {node_id}")
                    current_function = None
                    id_to_node = {}
                    node_id += 1
                    i += 1
                    continue
                
                # Identify SSA identifiers
                match_ssa = re.match(r"(%\w+)\s*=\s*\w+\s+.*", instr)
                if match_ssa:
                    ssa_id = match_ssa.group(1)
                    id_to_node[ssa_id] = node_id
                    node_to_id[node_id] = f"{current_function}_{ssa_id}"
                    print(f"SSA {ssa_id} defined at node {node_id} in {current_function}")
                
                print(f"Parsed: node={node_id}, instr_id={node_to_id[node_id]}, instr={instr}, features=[in_loop: {in_loop}, dist: {dist}]")
                
                # Handle dependencies
                if i + 1 < len(lines) and "Depends on:" in lines[i + 1]:
                    i += 1
                    dep_line = lines[i].strip()
                    deps = re.findall(r"(%\w+)", dep_line.split("Depends on:")[1])
                    for dep in deps:
                        dep_node = id_to_node.get(dep)
                        if dep_node is not None:
                            dependencies[node_id].add(dep_node)
                            print(f"Dependency: {node_id} -> {dep_node} (for {dep})")
                        else:
                            print(f"Warning: Dependency {dep} not found for node {node_id}")
                
                node_id += 1
                i += 1
            
            # Build memory operations
            mem_ops = defaultdict(lambda: {'writes': set(), 'reads': set()})
            for nid, instr in instr_text.items():
                parts = instr.split()
                for j, part in enumerate(parts):
                    if part == 'store' and j + 2 < len(parts):
                        addr_match = re.search(r"ptr %(\d+)", parts[j + 2])
                        if addr_match:
                            mem_addr = f"{func_map[nid]}_%{addr_match.group(1)}"
                            mem_ops[mem_addr]['writes'].add(nid)
                            print(f"Store to {mem_addr} by node {nid}")
                    elif part == 'load' and j + 2 < len(parts):
                        addr_match = re.search(r"ptr %(\d+)", parts[j + 2])
                        if addr_match:
                            mem_addr = f"{func_map[nid]}_%{addr_match.group(1)}"
                            mem_ops[mem_addr]['reads'].add(nid)
                            print(f"Load from {mem_addr} by node {nid}")
                    elif part == 'alloca':
                        write_match = re.search(r"%(\d+)", parts[j - 1]) if j > 0 else None
                        if write_match:
                            mem_addr = f"{func_map[nid]}_%{write_match.group(1)}"
                            mem_ops[mem_addr]['writes'].add(nid)
                            print(f"Alloc to {mem_addr} by node {nid}")
    
    except Exception as e:
        print(f"Error parsing {cf_file}: {e}")
        return {}, {}, {}, {}, {}, {}, {}, {}, {}
    
    print(f"node_to_id: {node_to_id}")
    print(f"label_to_start: {label_to_start}")
    print(f"function_to_head_and_tail: {function_to_head_and_tail}")
    print(f"mem_ops: {dict(mem_ops)}")
    print(f"dependencies: {dict(dependencies)}")
    return cf_data, instr_text, label_to_start, function_to_head_and_tail, function_scopes, func_map, instr_order, node_to_id, mem_ops, dependencies

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
        return {}
    
    history_features = {}
    for branch_id, outcomes in branch_outcomes.items():
        n = len(outcomes)
        taken_prob = sum(outcomes) / n if n > 0 else 0.0
        geo = [sum(outcomes[-l:]) / l if n >= l else taken_prob for l in [2, 4, 8]]
        history_features[branch_id] = [taken_prob] + geo
        print(f"Branch {branch_id}: taken_prob={taken_prob}, geo={geo}")
    return history_features

def build_edge_features(cf_data, instr_text, label_to_start, function_to_head_and_tail, function_scopes, func_map, instr_order, node_to_id, mem_ops, bh_data, dependencies, max_dist=100):
    """Build edge features using generate_xfg logic with branch probabilities and dependencies."""
    edge_features = {}
    branch_counter = 0
    branch_mapping = {}  # branch_id: node_id
    
    # Second Pass: Add control and data edges
    for i in instr_text:
        instr = instr_text[i]
        if i not in cf_data or not isinstance(instr, str):
            print(f"Warning: Skipping node {i} (instr={instr})")
            continue
        if re.match(r"\d+:\s*;.*", instr) or instr.startswith("define"):
            continue
        
        node_function = func_map.get(i)
        dist_to_branch = min(cf_data[i][1] / max_dist, 1.0)
        src_in_loop = cf_data[i][0]
        
        # Data dependencies: SSA register uses from dependencies
        for dep_node in dependencies[i]:
            edge_features[(dep_node, i)] = [dist_to_branch, 0.0, 0.0, 0.0, cf_data[dep_node][0], src_in_loop, 1, 4, "DATA-FLOW"]
            print(f"Data edge (dependency): {dep_node} -> {i}")
        
        # Data dependencies: SSA register uses from operands
        operands = re.findall(r"(%\w+)", instr)
        if node_function and operands:
            id_to_node = function_scopes.get(node_function, {})
            for op in operands:
                if op in id_to_node and id_to_node[op] != i and id_to_node[op] not in dependencies[i]:
                    tgt_node = id_to_node[op]
                    edge_features[(tgt_node, i)] = [dist_to_branch, 0.0, 0.0, 0.0, cf_data[tgt_node][0], src_in_loop, 1, 4, "DATA-FLOW"]
                    print(f"Data edge (SSA): {tgt_node} -> {i}")
        
        # Control flow: Sequential edges
        if i > 0 and i-1 in instr_text and isinstance(instr_text[i-1], str) and not instr_text[i-1].startswith(("br ", "ret ")) and "= call" not in instr_text[i-1]:
            if re.match(r"\d+:\s*;.*", instr_text[i-1]) or instr_text[i-1].startswith("define"):
                continue
            prev_function = func_map.get(i-1)
            if node_function == prev_function:
                edge_features[(i-1, i)] = [dist_to_branch, 0.0, 0.0, 0.0, cf_data[i-1][0], src_in_loop, 0, 0, "CONTROL-FLOW"]
                print(f"Sequential edge: {i-1} -> {i}")
        
        # Control flow: Branch edges
        if instr.startswith("br ") and node_function:
            branch_id = branch_counter
            branch_mapping[branch_id] = i
            branch_counter += 1
            targets = re.findall(r"label\s+%(\w+)", instr)
            history = bh_data.get(branch_id, [0.0, 0.0, 0.0, 0.0])
            print(f"Branch mapping: branch_id={branch_id}, node={i}, history={history}")
            for idx, target in enumerate(targets):
                target_label = f"%{target}"
                if (node_function, target_label) in label_to_start:
                    tgt_node = label_to_start[(node_function, target_label)]
                    edge_type = 1 if idx == 0 and len(targets) == 2 else 2 if idx == 1 else 3
                    geo = [h if edge_type == 1 else 1.0 - h for h in history[1:]] if len(targets) == 2 else [0.0, 0.0, 0.0]
                    edge_features[(i, tgt_node)] = [dist_to_branch, *geo, src_in_loop, cf_data.get(tgt_node, [0, 0, 0, 0, 0])[0], 0, edge_type, "CONTROL-FLOW"]
                    print(f"Branch edge: {i} -> {tgt_node}, edge_type={edge_type}, geo={geo}")
                else:
                    print(f"Warning: Target label {target_label} not found in {node_function}")
        
        # Control flow: Call edges
        match_call = re.match(r".*call.*@(\w+)", instr)
        if match_call and node_function:
            called_function = match_call.group(1)
            if called_function in function_to_head_and_tail:
                head, tail = function_to_head_and_tail[called_function]
                edge_features[(i, head)] = [dist_to_branch, 0.0, 0.0, 0.0, src_in_loop, cf_data.get(head, [0, 0, 0, 0, 0])[0], 0, 7, "CONTROL-FLOW"]
                print(f"Call edge: {i} -> {head}")
                if tail is not None and i + 1 in instr_text and isinstance(instr_text[i + 1], str) and not instr_text[i + 1].startswith(("define", "ret ")):
                    if not re.match(r"\d+:\s*;.*", instr_text[i + 1]):
                        next_function = func_map.get(i + 1)
                        if node_function == next_function:
                            edge_features[(tail, i + 1)] = [dist_to_branch, 0.0, 0.0, 0.0, cf_data.get(tail, [0, 0, 0, 0, 0])[0], src_in_loop, 0, 8, "CONTROL-FLOW"]
                            print(f"Return edge: {tail} -> {i + 1}")
    
    # Memory dependencies
    for mem_addr, ops in mem_ops.items():
        # RAW: alloca -> store
        for alloc_id in ops['writes']:
            if 'alloca' not in instr_text.get(alloc_id, ""):
                continue
            dist_to_branch_dep = min(cf_data[alloc_id][1] / max_dist, 1.0)
            src_in_loop_dep = cf_data[alloc_id][0]
            for store_id in ops['writes']:
                if 'store' not in instr_text.get(store_id, "") or store_id == alloc_id:
                    continue
                edge_features[(alloc_id, store_id)] = [dist_to_branch_dep, 0.0, 0.0, 0.0, src_in_loop_dep, cf_data[store_id][0], 1, 4, "DATA-FLOW"]
                print(f"Memory edge (RAW, alloca->store): {alloc_id} -> {store_id}, mem_addr={mem_addr}")
        
        # RAW: store -> load
        for store_id in ops['writes']:
            if 'store' not in instr_text.get(store_id, ""):
                continue
            dist_to_branch_dep = min(cf_data[store_id][1] / max_dist, 1.0)
            src_in_loop_dep = cf_data[store_id][0]
            for load_id in ops['reads']:
                if load_id == store_id:
                    continue
                edge_features[(store_id, load_id)] = [dist_to_branch_dep, 0.0, 0.0, 0.0, src_in_loop_dep, cf_data[load_id][0], 1, 4, "DATA-FLOW"]
                print(f"Memory edge (RAW, store->load): {store_id} -> {load_id}, mem_addr={mem_addr}")
        
        # WAR: load -> store
        for load_id in ops['reads']:
            dist_to_branch_dep = min(cf_data[load_id][1] / max_dist, 1.0)
            src_in_loop_dep = cf_data[load_id][0]
            for store_id in ops['writes']:
                if 'store' not in instr_text.get(store_id, "") or store_id == load_id:
                    continue
                edge_features[(load_id, store_id)] = [dist_to_branch_dep, 0.0, 0.0, 0.0, src_in_loop_dep, cf_data[store_id][0], 2, 5, "DATA-FLOW"]
                print(f"Memory edge (WAR): {load_id} -> {store_id}, mem_addr={mem_addr}")
    
    return edge_features, branch_mapping

def merge_features_for_corpus(ll_dir="dsa/dsa/llvm", cf_dir="control_flow_features", bh_dir="branch_history_logs", output_file="edge_features.txt"):
    corpus_data = {}
    
    ll_files = glob.glob(f"{ll_dir}/*.ll")
    with open(output_file, 'w') as f:
        f.write(f"Found {len(ll_files)} LLVM IR files to process\n")
        for i, ll_file in enumerate(ll_files):
            base_name = os.path.basename(ll_file).replace('.ll', '')
            cf_file = f"{cf_dir}/{base_name}_control_flow_features.txt"
            bh_file = f"{bh_dir}/{base_name}_branch_history.log"
            
            f.write(f"Processing {i+1}/{len(ll_files)}: {base_name}\n")
            if not os.path.exists(cf_file) or not os.path.exists(bh_file):
                f.write(f"Warning: Missing files for {base_name}, skipping\n")
                continue
            
            cf_data, instr_text, label_to_start, function_to_head_and_tail, function_scopes, func_map, instr_order, node_to_id, mem_ops, dependencies = parse_control_flow(cf_file)
            if not cf_data:
                f.write(f"Warning: No data parsed for {base_name}, skipping\n")
                continue
            
            bh_data = parse_branch_history(bh_file)
            
            edge_features, branch_mapping = build_edge_features(
                cf_data, instr_text, label_to_start, function_to_head_and_tail, function_scopes, func_map, instr_order, node_to_id, mem_ops, bh_data, dependencies, max_dist=100
            )
            
            corpus_data[base_name] = {
                "edge_features": edge_features,
                "node_to_id": node_to_id,
                "instr_text": instr_text,
                "branch_mapping": branch_mapping
            }
        
        for program_name, data in corpus_data.items():
            f.write(f"\nProgram: {program_name}\n")
            if not data["edge_features"]:
                f.write("  No edges generated\n")
            for (src_node, tgt_node), feat in sorted(data["edge_features"].items()):
                src_instr = data["instr_text"].get(src_node, "Unknown")
                tgt_instr = data["instr_text"].get(tgt_node, "Unknown")
                f.write(f"  Edge {src_node} -> {tgt_node} (\"{src_instr}\" -> \"{tgt_instr}\"): {feat}\n")
    
    with open(output_file, 'r') as f:
        print(f.read())
    
    return corpus_data

if __name__ == "__main__":
    merge_features_for_corpus()
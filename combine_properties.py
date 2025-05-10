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
    branch_ids = {}  # node_id: BranchID
    current_function = None
    id_to_node = {}  # SSA identifiers for current function
    node_id = 0
    
    # First pass: Collect branch targets
    branch_targets = defaultdict(list)  # (func_name, label) -> [node_ids]
    try:
        with open(cf_file, 'r') as f:
            lines = f.readlines()
            i = 0
            while i < len(lines):
                line = lines[i].strip()
                if not line:
                    i += 1
                    continue
                
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
                
                # Parse instruction and features
                match = re.match(r"(?:BranchID: (\d+)\s+)?(.+?)(?:\[in_loop: (\d), dist_to_branch: (\d+)\]|$)", line)
                if not match:
                    print(f"Warning: Skipping unparseable line: {line}")
                    i += 1
                    continue
                
                branch_id, instr, in_loop, dist = match.groups()
                instr = instr.strip()
                if instr.startswith("br "):
                    targets = re.findall(r"label\s+%(\w+)", instr)
                    for target in targets:
                        branch_targets[(current_function, f"%{target}")].append(node_id)
                
                i += 1
            
            # Second pass: Process instructions and assign labels
            i = 0
            node_id = 0
            current_function = None
            id_to_node = {}
            while i < len(lines):
                line = lines[i].strip()
                if not line:
                    i += 1
                    continue
                
                if line.startswith("Control-flow features for function:"):
                    current_function = line.split(":")[1].strip()
                    function_to_head_and_tail[current_function] = [node_id, None]
                    id_to_node = {}
                    function_scopes[current_function] = id_to_node
                    i += 1
                    continue
                
                if not current_function:
                    i += 1
                    continue
                
                # Handle explicit labels
                match_label = re.match(r"(\d+):", line)
                if match_label:
                    label = f"%{match_label.group(1)}"
                    label_to_start[(current_function, label)] = node_id
                    print(f"Label {label} mapped to node {node_id} in {current_function}")
                    i += 1
                    continue
                
                # Parse instruction and features
                match = re.match(r"(?:BranchID: (\d+)\s+)?(.+?)(?:\[in_loop: (\d), dist_to_branch: (\d+)\]|$)", line)
                if not match:
                    print(f"Warning: Skipping unparseable line: {line}")
                    i += 1
                    continue
                
                branch_id, instr, in_loop, dist = match.groups()
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
                
                if branch_id and instr.startswith("br "):
                    branch_ids[node_id] = int(branch_id)
                    print(f"BranchID {branch_id} assigned to node {node_id}")
                
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
                    deps = [dep.strip() for dep in dep_line.split("Depends on:")[1].split(",") if dep.strip()]
                    for dep in deps:
                        dep_match = re.match(r"(%\w+)", dep)
                        if dep_match:
                            dep_id = dep_match.group(1)
                            dep_node = id_to_node.get(dep_id)
                            if dep_node is not None and dep_node != node_id:
                                dependencies[node_id].add(dep_node)
                                print(f"Dependency: {node_id} -> {dep_node} (for {dep_id})")
                            else:
                                print(f"Warning: Dependency {dep_id} not found or self-loop for node {node_id}")
                
                node_id += 1
                i += 1
            
            # Assign labels to branch targets
            for (func_name, label), nodes in branch_targets.items():
                target_node = min(n for n in instr_text.keys() if n > min(nodes)) if nodes else None
                if target_node in instr_text and (func_name, label) not in label_to_start:
                    label_to_start[(func_name, label)] = target_node
                    print(f"Assigned label {label} to node {target_node} in {func_name}")
            
            # Build memory operations (exclude alloca)
            mem_ops = defaultdict(lambda: {'writes': set(), 'reads': set()})
            for nid, instr in instr_text.items():
                if any(op in instr for op in ['store', 'load']):
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
    
    except Exception as e:
        print(f"Error parsing {cf_file}: {e}")
        return {}, {}, {}, {}, {}, {}, {}, {}, {}, {}, {}
    
    print(f"node_to_id: {node_to_id}")
    print(f"label_to_start: {label_to_start}")
    print(f"function_to_head_and_tail: {function_to_head_and_tail}")
    print(f"mem_ops: {dict(mem_ops)}")
    print(f"dependencies: {dict(dependencies)}")
    print(f"branch_ids: {branch_ids}")
    return cf_data, instr_text, label_to_start, function_to_head_and_tail, function_scopes, func_map, instr_order, node_to_id, mem_ops, dependencies, branch_ids

def parse_branch_history(bh_file):
    """Parse branch_history.log for edge-relevant dynamic features."""
    branch_outcomes = defaultdict(list)
    try:
        with open(bh_file, 'r') as f:
            next(f)  # Skip header
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

def build_edge_features(cf_data, instr_text, label_to_start, function_to_head_and_tail, function_scopes, func_map, instr_order, node_to_id, mem_ops, bh_data, dependencies, branch_ids, max_dist=100):
    """Build edge features using generate_xfg logic with correct branch probabilities."""
    edge_features = {}
    branch_mapping = {}
    
    for i in sorted(instr_text.keys()):
        instr = instr_text[i]
        if i not in cf_data or not isinstance(instr, str):
            print(f"Warning: Skipping node {i} (instr={instr})")
            continue
        if re.match(r"\d+:\s*;.*", instr) or instr.startswith("define"):
            continue
        
        node_function = func_map.get(i)
        dist_to_branch = min(cf_data[i][1] / max_dist, 1.0)
        src_in_loop = cf_data[i][0]
        
        # Data dependencies: Only from explicit SSA dependencies
        for dep_node in dependencies[i]:
            edge_features[(dep_node, i)] = [dist_to_branch, 0.0, 0.0, 0.0, cf_data[dep_node][0], src_in_loop, 1, 4, "DATA-FLOW"]
            print(f"Data edge (dependency): {dep_node} -> {i}")
        
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
            branch_id = branch_ids.get(i, -1)
            branch_mapping[branch_id] = i
            targets = re.findall(r"label\s+%(\w+)", instr)
            history = bh_data.get(branch_id, [0.0, 0.0, 0.0, 0.0])
            print(f"Branch mapping: branch_id={branch_id}, node={i}, history={history}, targets={targets}")
            for idx, target in enumerate(targets):
                target_label = f"%{target}"
                if (node_function, target_label) in label_to_start:
                    tgt_node = label_to_start[(node_function, target_label)]
                    edge_type = 1 if idx == 0 and len(targets) == 2 else 2 if idx == 1 else 3
                    geo = history[1:] if idx == 0 and len(targets) == 2 else [1.0 - h for h in history[1:]] if idx == 1 and len(targets) == 2 else [0.0, 0.0, 0.0]
                    edge_features[(i, tgt_node)] = [dist_to_branch, *geo, src_in_loop, cf_data.get(tgt_node, [0, 0, 0, 0, 0])[0], 0, edge_type, "CONTROL-FLOW"]
                    print(f"Branch edge: {i} -> {tgt_node}, edge_type={edge_type}, geo={geo}, instr={instr} -> {instr_text.get(tgt_node, 'Unknown')}")
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
    
        # Memory dependencies: Only RAW (store -> load)
        for mem_addr, ops in mem_ops.items():
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
            
            cf_data, instr_text, label_to_start, function_to_head_and_tail, function_scopes, func_map, instr_order, node_to_id, mem_ops, dependencies, branch_ids = parse_control_flow(cf_file)
            if not cf_data:
                f.write(f"Warning: No data parsed for {base_name}, skipping\n")
                continue
            
            bh_data = parse_branch_history(bh_file)
            
            edge_features, branch_mapping = build_edge_features(
                cf_data, instr_text, label_to_start, function_to_head_and_tail, function_scopes, func_map, instr_order, node_to_id, mem_ops, bh_data, dependencies, branch_ids, max_dist=100
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
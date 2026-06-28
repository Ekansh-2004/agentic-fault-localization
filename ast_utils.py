# ast_utils.py
import os
import ast

def discover_classes_in_workspace(directory="."):
    """Walks the workspace directory, parses Python files via AST, and extracts all class information."""
    classes_info = []
    for root, dirs, files in os.walk(directory):
        # Exclude directories we don't want to scan
        dirs[:] = [d for d in dirs if d not in ('venv', '.git', '__pycache__')]
        for file in files:
            if file.endswith('.py') and file not in ('debugger.py', 'ast_utils.py', 'rag_engine.py', 'repair_engine.py', 'temp_test_runner.py'):
                file_path = os.path.join(root, file)
                try:
                    with open(file_path, 'r', encoding='utf-8') as f:
                        content = f.read()
                    
                    tree = ast.parse(content)
                    for node in ast.walk(tree):
                        if isinstance(node, ast.ClassDef):
                            class_name = node.name
                            docstring = ast.get_docstring(node) or f"Class {class_name} defined in {file}."
                            
                            # Extract raw class code block
                            class_lines = content.split('\n')
                            start_line = node.lineno - 1
                            end_line = getattr(node, 'end_lineno', len(class_lines))
                            class_code = '\n'.join(class_lines[start_line:end_line])
                            
                            # Extract list of method definitions
                            methods = [n.name for n in node.body if isinstance(n, ast.FunctionDef)]
                            
                            classes_info.append({
                                'class_name': class_name,
                                'file_path': file_path,
                                'docstring': docstring,
                                'code': class_code,
                                'methods': methods
                            })
                except Exception as e:
                    print(f"⚠️ Warning: Failed to parse {file_path}: {e}")
    return classes_info

def find_internal_dependencies(class_code, method_name):
    """Parses class source code using AST to find self.method_name(...) calls inside the target method."""
    try:
        tree = ast.parse(class_code)
        target_fn = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == method_name:
                target_fn = node
                break
        
        if not target_fn:
            return []
            
        dependencies = []
        for node in ast.walk(target_fn):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if isinstance(node.func.value, ast.Name) and node.func.value.id == 'self':
                    dependencies.append(node.func.attr)
        return list(set(dependencies))
    except Exception as e:
        print(f"⚠️ Error tracing dependencies: {e}")
        return []

def find_method_line_range(file_path, class_name, method_name):
    """Parses a file and returns the line range (0-indexed start, 1-indexed end) of a specific method."""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        tree = ast.parse(content)
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == class_name:
                for subnode in node.body:
                    if isinstance(subnode, ast.FunctionDef) and subnode.name == method_name:
                        start_line = subnode.lineno - 1
                        end_line = getattr(subnode, 'end_lineno', len(content.split('\n')))
                        return start_line, end_line
    except Exception as e:
        print(f"⚠️ Error finding method line range: {e}")
    return None, None

def extract_specific_function_body(class_code, function_name):
    """Slices a specific method's block out of a class's source code block."""
    lines = class_code.split("\n")
    target_block = []
    capture = False
    indent_level = None
    
    for line in lines:
        stripped = line.lstrip()
        # Find where function definition starts
        if stripped.startswith(f"def {function_name}("):
            capture = True
            target_block.append(line)
            # Calculate the indentation level of the method definition
            indent_level = len(line) - len(stripped)
            continue
            
        if capture:
            # If line is not empty and has an indent level less than or equal to the method definition's indent, stop
            if stripped:
                current_indent = len(line) - len(stripped)
                if current_indent <= indent_level and not line.startswith(" " * (indent_level + 1)):
                    break
            target_block.append(line)
            
    return "\n".join(target_block)

def find_cross_class_dependencies(file_path, class_code, method_name, discovered_classes):
    """
    Parses the target method and the containing file's imports using AST to find references 
    to other classes in the workspace.
    """
    try:
        # 1. Parse the containing file to extract all imports
        with open(file_path, 'r', encoding='utf-8') as f:
            file_content = f.read()
        
        file_tree = ast.parse(file_content)
        imports = {} # maps imported class name or module name to source info
        
        for node in ast.walk(file_tree):
            if isinstance(node, ast.Import):
                for name in node.names:
                    imports[name.name] = name.name
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                for name in node.names:
                    imports[name.name] = module
                    
        # 2. Parse the target method body
        class_tree = ast.parse(class_code)
        target_fn = None
        for node in ast.walk(class_tree):
            if isinstance(node, ast.FunctionDef) and node.name == method_name:
                target_fn = node
                break
                
        if not target_fn:
            return []
            
        referenced_classes = []
        
        # 3. Look for referenced class names in target method AST
        for node in ast.walk(target_fn):
            # Check names (e.g. UserService() -> Name 'UserService')
            if isinstance(node, ast.Name):
                name = node.id
                if name in imports:
                    imported_from = imports[name]
                    for cls in discovered_classes:
                        if cls['class_name'].lower() == name.lower() or os.path.splitext(os.path.basename(cls['file_path']))[0].lower() == imported_from.lower():
                            referenced_classes.append(cls)
                            
            # Check attribute accesses (e.g. user_service.get_user_balance())
            elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                obj_name = node.value.id
                if obj_name in imports:
                    imported_module = imports[obj_name]
                    for cls in discovered_classes:
                        if os.path.splitext(os.path.basename(cls['file_path']))[0].lower() == imported_module.lower():
                            referenced_classes.append(cls)
                            
        # De-duplicate
        unique_referenced_classes = []
        seen = set()
        for cls in referenced_classes:
            key = (cls['class_name'], cls['file_path'])
            if key not in seen:
                seen.add(key)
                unique_referenced_classes.append(cls)
                
        return unique_referenced_classes
    except Exception as e:
        print(f"⚠️ Error finding cross-class dependencies: {e}")
        return []

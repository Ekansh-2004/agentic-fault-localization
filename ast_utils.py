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

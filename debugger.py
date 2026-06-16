# debugger.py
import os
import ast
import re
import sys
import subprocess
import textwrap
import chromadb
from groq import Groq
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

def discover_classes_in_workspace(directory="."):
    """Walks the workspace directory, parses Python files via AST, and extracts all class information."""
    classes_info = []
    for root, dirs, files in os.walk(directory):
        # Exclude directories we don't want to scan
        dirs[:] = [d for d in dirs if d not in ('venv', '.git', '__pycache__')]
        for file in files:
            if file.endswith('.py') and file != 'debugger.py':
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

def apply_patch_to_file(file_path, start_line, end_line, new_code):
    """Replaces target lines in a file with the patched code block, matching target indentation."""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        
        # Read the indentation level of the method we are replacing
        original_first_line = lines[start_line]
        indentation = len(original_first_line) - len(original_first_line.lstrip())
        
        # Adjust new_code indentation if necessary
        new_lines = new_code.split('\n')
        non_empty_lines = [l for l in new_lines if l.strip()]
        if non_empty_lines:
            min_indent = min(len(l) - len(l.lstrip()) for l in non_empty_lines)
            adjusted_new_lines = []
            for line in new_lines:
                if line.strip():
                    adjusted_new_lines.append(" " * indentation + line[min_indent:])
                else:
                    adjusted_new_lines.append("")
            patched_code = '\n'.join(adjusted_new_lines)
        else:
            patched_code = new_code

        # Replace lines in original list
        lines[start_line:end_line] = [patched_code + '\n']
        
        with open(file_path, 'w', encoding='utf-8') as f:
            f.writelines(lines)
        return True
    except Exception as e:
        print(f"⚠️ Error applying patch: {e}")
        return False

def verify_syntax(file_path):
    """Compiles the source file using the current Python environment to verify syntax."""
    try:
        result = subprocess.run(
            [sys.executable, '-m', 'py_compile', file_path],
            capture_output=True,
            text=True
        )
        if result.returncode == 0:
            return True, ""
        else:
            return False, result.stderr
    except Exception as e:
        return False, str(e)

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

def extract_patch(text):
    """Helper to extract contents between [PATCH] and [/PATCH] tags."""
    match = re.search(r'\[PATCH\](.*?)\[/PATCH\]', text, re.DOTALL)
    if match:
        return match.group(1).strip()
    return None

def get_patched_method_name(patch_code):
    """Parses patch code using AST/Regex to detect the name of the function defined inside."""
    try:
        try:
            tree = ast.parse(patch_code)
        except SyntaxError:
            dedented = textwrap.dedent(patch_code)
            tree = ast.parse(dedented)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                return node.name
    except Exception:
        pass
    # Regex fallback
    match = re.search(r'def\s+(\w+)\s*\(', patch_code)
    if match:
        return match.group(1)
    return None

def main():
    if not os.environ.get("GROQ_API_KEY"):
        print("❌ Error: GROQ_API_KEY environment variable not found.")
        return

    client = Groq()
    CLOUD_MODEL = 'llama-3.1-8b-instant'
    
    runtime_stack_trace = """
    Traceback (most recent call last):
      File "main.py", line 45, in <module>
        payment_service.apply_processing_fee()
      File "payment_service.py", line 22, in apply_processing_fee
        final_total = current_balance + fixed_fee
    TypeError: unsupported operand type(s) for +: 'str' and 'float'
    """
    
    print("==================================================")
    print("STARTING VERSION 7.0: DEPENDENCY-AWARE AGENTIC DEBUGGER")
    print("==================================================\n")

    # STEP 1: WORKSPACE DISCOVERY
    print("📋 [STEP 1] Scanning workspace for Python classes...")
    discovered_classes = discover_classes_in_workspace(".")
    print(f"Found {len(discovered_classes)} classes:")
    for cls in discovered_classes:
        print(f" - {cls['class_name']} (in {cls['file_path']}) with methods: {cls['methods']}")
    print()

    # STEP 2: VECTOR DB INDEXING
    print("🗂️ [STEP 2] Initializing local ChromaDB and indexing class documentation...")
    chroma_client = chromadb.Client()
    
    # Reset/Create Collection
    collection_name = "class_documentations"
    try:
        chroma_client.delete_collection(name=collection_name)
    except Exception:
        pass
    collection = chroma_client.create_collection(name=collection_name)
    
    documents = []
    metadatas = []
    ids = []
    
    for i, cls in enumerate(discovered_classes):
        documents.append(cls['docstring'])
        metadatas.append({
            'class_name': cls['class_name'],
            'file_path': cls['file_path'],
            'methods': ','.join(cls['methods']),
        })
        ids.append(f"class_{i}")
        
    if documents:
        collection.add(
            documents=documents,
            metadatas=metadatas,
            ids=ids
        )
        print(f"Successfully indexed {len(documents)} classes in ChromaDB.\n")
    else:
        print("❌ Error: No classes found to index.\n")
        return

    # STEP 3: RAG RETRIEVAL
    print("🔍 [STEP 3] Querying Vector DB with the traceback log...")
    query_results = collection.query(
        query_texts=[runtime_stack_trace],
        n_results=min(3, len(discovered_classes))
    )
    
    retrieved_classes = []
    print("Top retrieved classes:")
    if query_results and 'metadatas' in query_results and query_results['metadatas']:
        for idx, metadata in enumerate(query_results['metadatas'][0]):
            class_name = metadata['class_name']
            file_path = metadata['file_path']
            # Find in discovered list
            cls_obj = next((c for c in discovered_classes if c['class_name'] == class_name), None)
            if cls_obj:
                retrieved_classes.append(cls_obj)
                print(f" {idx + 1}. {class_name} ({file_path})")
    print()

    if not retrieved_classes:
        print("❌ Error: No classes retrieved from vector DB.")
        return

    # STAGE 1: CODEBASE NAVIGATION (The Software Architect)
    print("--- [STAGE 1] Running Codebase Navigation on Retrieved Classes ---")
    formatted_classes = []
    for cls in retrieved_classes:
        formatted_classes.append(f"Class: {cls['class_name']} in {cls['file_path']}\nMethods: {', '.join(cls['methods'])}")
    
    retrieved_context = "\n".join(formatted_classes)
    navigator_prompt = f"""You are an expert Software Architect. Analyze the following runtime crash stack trace and the list of retrieved classes from our Vector DB.
Determine which specific class and method contains the buggy logic that directly triggered this traceback error.

Runtime Crash Log:
\"\"\"
{runtime_stack_trace}
\"\"\"

Retrieved Classes Context:
\"\"\"
{retrieved_context}
\"\"\"

Respond with the exact class name and function name separated by a dot, e.g. ClassName.method_name.
Respond with ONLY this identifier. Do not write any explanations, markdown backticks, or punctuation."""

    nav_completion = client.chat.completions.create(model=CLOUD_MODEL, messages=[{"role": "user", "content": navigator_prompt}])
    predicted_target = nav_completion.choices[0].message.content.strip().replace("`", "")
    print(f"🎯 Architect isolated target: '{predicted_target}'\n")

    if '.' not in predicted_target:
        print(f"❌ Error: Architect responded with invalid target pattern '{predicted_target}'")
        return
        
    class_part, method_part = predicted_target.split('.', 1)
    
    # Find the target class object using case-insensitive match, with a fallback to file name matching
    class_part_lower = class_part.lower()
    target_class = next((c for c in discovered_classes if c['class_name'].lower() == class_part_lower), None)
    
    if not target_class:
        # Fallback: check if the predicted name matches the base filename (e.g., "payment_service" matching PaymentService in payment_service.py)
        target_class = next((c for c in discovered_classes if os.path.splitext(os.path.basename(c['file_path']))[0].lower() == class_part_lower), None)

    if not target_class:
        print(f"❌ Error: Could not find class or module matching '{class_part}' in discovered classes.")
        return
        
    # TOOL STEP: CONTEXT REDUCTION (Now with Dependency-Aware Slicing)
    print("--- [TOOL USE] Programmatically Slicing Targeted Code & Dependencies ---")
    
    # AST Trace dependency calls
    dependencies = find_internal_dependencies(target_class['code'], method_part)
    print(f"🔗 Traced dependency calls inside class: {dependencies}")
    
    primary_code = extract_specific_function_body(target_class['code'], method_part)
    dep_snippets = []
    for dep in dependencies:
        dep_code = extract_specific_function_body(target_class['code'], dep)
        if dep_code:
            dep_snippets.append(f"# Dependency Method:\n{dep_code}")
            
    if dep_snippets:
        isolated_code_snippet = primary_code + "\n\n" + "\n\n".join(dep_snippets)
    else:
        isolated_code_snippet = primary_code
        
    print(f"Isolated Code Context:\n{isolated_code_snippet}\n")

    # STAGE 1.5: METHOD DOCUMENTATION ENHANCEMENT (The Source Code Reviewer)
    print("--- [STAGE 1.5] Running Method Doc Enhancement ---")
    enhancement_prompt = f"""You are a Source Code Reviewer. Analyze the following method/class snippet and trace its internal dependencies or external function calls. 
Generate an enhanced, highly technical docstring comment for this function/class that clearly documents what data types are processed and any risks of nested function values causing type mismatches.

Code:
{isolated_code_snippet}

Respond with ONLY the text of the new docstring comment. Do not include markdown code block formatting."""

    doc_completion = client.chat.completions.create(model=CLOUD_MODEL, messages=[{"role": "user", "content": enhancement_prompt}])
    enhanced_docstring = doc_completion.choices[0].message.content.strip()
    print("📝 Successfully generated enhanced architectural metadata docstring.\n")

    # STAGE 2: FAULT CONFIRMATION & PATCH GENERATION (The Test Engineer)
    print("--- [STAGE 2] Running Fault Confirmation & Patch Generation ---")
    confirmation_prompt = f"""You are a Software Test Engineer. Review the code snippet along with its dependencies and enhanced architectural documentation to resolve this traceback crash:
{runtime_stack_trace}

Enhanced Documentation Context:
\"\"\"
{enhanced_docstring}
\"\"\"

Function/Class Code Base & Dependencies:
```python
{isolated_code_snippet}
```

Identify the bug, explain how the enhanced documentation confirms the error.
Select the best method/function to modify (either the main method, or one of the helper dependency methods like 'fetch_user_balance' that is returning an incorrect type).
You MUST provide the final corrected version of the method/function code wrapped in [PATCH] and [/PATCH] tags. For example:
[PATCH]
    def fetch_user_balance(self):
        # your corrected code here returning a float/int instead of a string
[/PATCH]
Do not put anything else inside the [PATCH] tags except the python function definition."""

    confirm_completion = client.chat.completions.create(model=CLOUD_MODEL, messages=[{"role": "user", "content": confirmation_prompt}])
    confirm_text = confirm_completion.choices[0].message.content
    print("Generated fix suggestions.")

    # STAGE 3: AUTO-REPAIR & VERIFICATION (The Build & Release Engineer)
    print("--- [STAGE 3] Running Auto-Repair & Verification ---")
    
    max_retries = 3
    current_attempt = 1
    repaired_successfully = False
    
    while current_attempt <= max_retries:
        patch_code = extract_patch(confirm_text)
        if not patch_code:
            print("❌ Error: Could not extract [PATCH] block from LLM response.")
            correction_prompt = f"""You failed to provide the patch code inside the [PATCH] and [/PATCH] tags.
Please provide the corrected python code block inside [PATCH] and [/PATCH] tags."""
            confirm_completion = client.chat.completions.create(model=CLOUD_MODEL, messages=[
                {"role": "user", "content": confirmation_prompt},
                {"role": "assistant", "content": confirm_text},
                {"role": "user", "content": correction_prompt}
            ])
            confirm_text = confirm_completion.choices[0].message.content
            current_attempt += 1
            continue
            
        patched_method = get_patched_method_name(patch_code)
        if not patched_method:
            print("❌ Error: Could not parse function name from the patch code.")
            return
            
        print(f"Applying extracted patch to dynamic target method '{patched_method}' in {target_class['file_path']}...")
        start_l, end_l = find_method_line_range(target_class['file_path'], target_class['class_name'], patched_method)
        
        if start_l is None or end_l is None:
            print(f"❌ Error: Could not find line range for the target method '{patched_method}'.")
            return
            
        # Keep a backup of the file content in case we need to roll back
        with open(target_class['file_path'], 'r', encoding='utf-8') as f:
            backup_content = f.read()
            
        if apply_patch_to_file(target_class['file_path'], start_l, end_l, patch_code):
            print("Patch applied. Verifying syntax correctness...")
            success, err_msg = verify_syntax(target_class['file_path'])
            if success:
                print("✅ Syntax verification SUCCEEDED! Code repaired successfully.")
                repaired_successfully = True
                break
            else:
                print("❌ Syntax verification FAILED!")
                print(f"Compiler Error:\n{err_msg}\n")
                
                # Rollback
                with open(target_class['file_path'], 'w', encoding='utf-8') as f:
                    f.write(backup_content)
                
                print(f"Attempt {current_attempt}/{max_retries} failed. Querying LLM for correction...")
                correction_prompt = f"""The previous patch failed syntax verification with the following compiler error:
\"\"\"
{err_msg}
\"\"\"

Please fix the syntax error and generate a new, correct code block wrapped in [PATCH] and [/PATCH] tags."""
                
                confirm_completion = client.chat.completions.create(model=CLOUD_MODEL, messages=[
                    {"role": "user", "content": confirmation_prompt},
                    {"role": "assistant", "content": confirm_text},
                    {"role": "user", "content": correction_prompt}
                ])
                confirm_text = confirm_completion.choices[0].message.content
                current_attempt += 1
        else:
            print("❌ Error: Failed to write patch to file.")
            break
            
    if repaired_successfully:
        print("\n==================== FINAL COMPREHENSIVE AI REPORT ====================")
        print("Status: Repaired & Verified")
        print(f"Modified File: {target_class['file_path']}")
        print(f"Modified Target Method: {patched_method}")
        print("\nApplied Patch Block:")
        print(patch_code)
        print("=======================================================================")
    else:
        print("❌ Error: Failed to repair code within maximum repair attempts.")

if __name__ == "__main__":
    main()
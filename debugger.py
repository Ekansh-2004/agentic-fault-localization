# debugger.py
import os
import ast
import chromadb
from groq import Groq
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Optional: Paste your key here directly if it isn't set in your terminal
# os.environ["GROQ_API_KEY"] = "your_actual_groq_api_key_here"

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
    print("STARTING VERSION 5.0: RAG-POWERED AGENTIC DEBUGGER")
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
    # We query using the stack trace as the semantic query to find matching class docstrings
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
    
    # Find the target class object
    target_class = next((c for c in discovered_classes if c['class_name'] == class_part), None)
    if not target_class:
        print(f"❌ Error: Could not find class '{class_part}' in discovered classes.")
        return
        
    # TOOL STEP: CONTEXT REDUCTION
    print("--- [TOOL USE] Programmatically Slicing Targeted Code ---")
    isolated_code_snippet = extract_specific_function_body(target_class['code'], method_part)
    if not isolated_code_snippet:
        print(f"❌ Error: Could not extract code block for method '{method_part}' in class '{class_part}'")
        # Fallback to class code
        isolated_code_snippet = target_class['code']
    print(f"Isolated Code snippet for {predicted_target}:\n{isolated_code_snippet}\n")

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

    # STAGE 2: FAULT CONFIRMATION (The Test Engineer)
    print("--- [STAGE 2] Running Fault Confirmation ---")
    confirmation_prompt = f"""You are a Software Test Engineer. Review the code snippet along with its enhanced architectural documentation to resolve this traceback crash:
    {runtime_stack_trace}

    Enhanced Documentation Context:
    \"\"\"
    {enhanced_docstring}
    \"\"\"

    Function/Class Code Base:
    ```python
    {isolated_code_snippet}
    ```
    Identify the bug, explain how the enhanced documentation confirms the type error, and provide the final corrected code block."""
    confirm_completion = client.chat.completions.create(model=CLOUD_MODEL, messages=[{"role": "user", "content": confirmation_prompt}])

    print("==================== FINAL COMPREHENSIVE AI REPORT ====================")
    print(confirm_completion.choices[0].message.content)
    print("=======================================================================")

if __name__ == "__main__":
    main()
# debugger.py
import os
import ast
from groq import Groq

# Optional: Paste your key here directly if it isn't set in your terminal
# os.environ["GROQ_API_KEY"] = "your_actual_groq_api_key_here"

def load_raw_code(file_path):
    """Opens a local file and returns its raw text content."""
    with open(file_path, "r") as f:
        return f.read()

def dynamic_extract_function_names(file_content):
    """Uses AST to dynamically scan the file and extract all function definitions."""
    tree = ast.parse(file_content)
    function_names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            function_names.append(node.name)
    return function_names

def extract_specific_function_body(file_content, function_name):
    """Programmatically extracts a specific function's block out of the file text."""
    lines = file_content.split("\n")
    target_block = []
    capture = False
    for line in lines:
        if line.startswith(f"def {function_name}("):
            capture = True
        elif capture and line.startswith("def "):
            break
        if capture:
            target_block.append(line)
    return "\n".join(target_block)

def main():
    if not os.environ.get("GROQ_API_KEY"):
        print("❌ Error: GROQ_API_KEY environment variable not found.")
        return

    client = Groq()
    CLOUD_MODEL = 'llama-3.1-8b-instant'
    target_file = "target_code.py"
    
    runtime_stack_trace = """
    Traceback (most recent call last):
      File "main.py", line 45, in <module>
        apply_processing_fee()
      File "target_code.py", line 18, in apply_processing_fee
        final_total = current_balance + fixed_fee
    TypeError: unsupported operand type(s) for +: 'str' and 'float'
    """
    
    print("==================================================")
    print("STARTING VERSION 4.0: PRODUCTION-LEVEL AGENTIC DEBUGGER")
    print("==================================================\n")

    # TOOL STEP: AST DISCOVERY
    full_code = load_raw_code(target_file)
    detected_functions = dynamic_extract_function_names(full_code)
    print(f"📋 [AST TOOL] Discovered functions: {detected_functions}\n")

    # STAGE 1: CODEBASE NAVIGATION (The Software Architect)
    print("--- [STAGE 1] Running Codebase Navigation ---")
    formatted_list = "\n".join([f"- {name}" for name in detected_functions])
    navigator_prompt = f"""You are an expert Software Architect. Analyze the following runtime crash stack trace and the list of available functions in the file.
Determine which specific function from the list contains the buggy logic that directly triggered this traceback error.

Runtime Crash Log:
\"\"\"
{runtime_stack_trace}
\"\"\"

Available Functions to select from:
{formatted_list}

Respond with ONLY the exact name of the single most suspicious function from the list. Do not write any explanations, markdown backticks, or punctuation."""

    nav_completion = client.chat.completions.create(model=CLOUD_MODEL, messages=[{"role": "user", "content": navigator_prompt}])
    predicted_function = nav_completion.choices[0].message.content.strip().replace("`", "")
    print(f"🎯 Architect isolated target function: '{predicted_function}'\n")

    # TOOL STEP: CONTEXT REDUCTION
    print("--- [TOOL USE] Programmatically Slicing Targeted Code ---")
    isolated_code_snippet = extract_specific_function_body(full_code, predicted_function)
    if not isolated_code_snippet:
        print(f"❌ Error: Could not extract code block for function '{predicted_function}'")
        return
    print("Isolated Code snippet : ",isolated_code_snippet)

    # STAGE 1.5: METHOD DOCUMENTATION ENHANCEMENT (The Source Code Reviewer)
    print("--- [STAGE 1.5] Running Method Doc Enhancement ---")
    enhancement_prompt = f"""You are a Source Code Reviewer. Analyze the following function snippet and trace its internal dependencies or external function calls. 
Generate an enhanced, highly technical docstring comment for this function that clearly documents what data types are processed and any risks of nested function values causing type mismatches.

Function Code:
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

Function Code Base:
```python
{isolated_code_snippet}
Identify the bug, explain how the enhanced documentation confirms the type error, and provide the final corrected code block."""
    confirm_completion = client.chat.completions.create(model=CLOUD_MODEL, messages=[{"role": "user", "content": confirmation_prompt}])

    print("==================== FINAL COMPREHENSIVE AI REPORT ====================")
    print(confirm_completion.choices[0].message.content)
    print("=======================================================================")

if __name__ == "__main__":
    main()
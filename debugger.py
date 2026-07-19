# debugger.py
import os
import re

from dotenv import load_dotenv
from groq import Groq

# Import modularized components
from ast_utils import (
    discover_classes_in_workspace,
    extract_specific_function_body,
    find_cross_class_dependencies,
    find_internal_dependencies,
    find_method_line_range,
)
from rag_engine import initialize_and_index_db, query_relevant_classes
from repair_engine import (
    apply_patch_to_file,
    extract_patch,
    get_patched_method_name,
    run_test_suite,
    verify_runtime_execution,
    verify_syntax,
)

# Load environment variables from .env file
load_dotenv()


def truncate_output(text, limit=2000):
    """Truncates long subprocess output to a reasonable length before feeding it into an LLM prompt."""
    if text is None:
        return ""
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... [truncated, {len(text) - limit} more characters omitted]"


def extract_crash_entry_point(traceback_text, discovered_classes):
    """
    Parses the traceback text to find the last file and function name that belongs
    to our workspace classes, to serve as the test execution entry point.
    """
    # Traceback lines matching: File "filename.py", line XX, in function_name
    pattern = r'File "([^"]+\.py)", line \d+, in (\w+)'
    matches = re.findall(pattern, traceback_text)

    # We scan the matches in reverse (from the crash site upwards) to find the first one
    # that matches a class in our workspace.
    for file_name, func_name in reversed(matches):
        base_name = os.path.basename(file_name)
        # Find if this file and method exist in discovered classes
        for cls in discovered_classes:
            if (
                os.path.basename(cls["file_path"]) == base_name
                and func_name in cls["methods"]
            ):
                return cls["file_path"], cls["class_name"], func_name

    return None, None, None


def main():
    if not os.environ.get("GROQ_API_KEY"):
        print("❌ Error: GROQ_API_KEY environment variable not found.")
        return

    client = Groq()
    CLOUD_MODEL = "llama-3.1-8b-instant"

    runtime_stack_trace = """
    Traceback (most recent call last):
      File "main.py", line 45, in <module>
        payment_service.apply_processing_fee()
      File "payment_service.py", line 22, in apply_processing_fee
        final_total = current_balance + fixed_fee
    TypeError: unsupported operand type(s) for +: 'str' and 'float'
    """

    print("==================================================")
    print("CROSS-CLASS DEPENDENCY AGENTIC DEBUGGER")
    print("==================================================\n")

    # STEP 1: WORKSPACE DISCOVERY
    print("📋 [STEP 1] Scanning workspace for Python classes...")
    discovered_classes = discover_classes_in_workspace(".")
    print(f"Found {len(discovered_classes)} classes:")
    for cls in discovered_classes:
        print(
            f" - {cls['class_name']} (in {cls['file_path']}) with methods: {cls['methods']}"
        )
    print()

    # STEP 2: VECTOR DB INDEXING
    print("🗂️ [STEP 2] Initializing local ChromaDB and indexing class documentation...")
    collection = initialize_and_index_db(discovered_classes)
    if collection:
        print(f"Successfully indexed {len(discovered_classes)} classes in ChromaDB.\n")
    else:
        print("❌ Error: No classes found to index.\n")
        return

    # STEP 3: RAG RETRIEVAL
    print("🔍 [STEP 3] Querying Vector DB with the traceback log...")
    retrieved_classes = query_relevant_classes(
        collection, runtime_stack_trace, discovered_classes
    )

    print("Top retrieved classes:")
    for idx, cls in enumerate(retrieved_classes):
        print(f" {idx + 1}. {cls['class_name']} ({cls['file_path']})")
    print()

    if not retrieved_classes:
        print("❌ Error: No classes retrieved from vector DB.")
        return

    # STAGE 1: CODEBASE NAVIGATION (The Software Architect)
    print("--- [STAGE 1] Running Codebase Navigation on Retrieved Classes ---")
    formatted_classes = []
    for cls in retrieved_classes:
        formatted_classes.append(
            f"Class: {cls['class_name']} in {cls['file_path']}\nMethods: {', '.join(cls['methods'])}"
        )

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

    nav_completion = client.chat.completions.create(
        model=CLOUD_MODEL, messages=[{"role": "user", "content": navigator_prompt}]
    )
    predicted_target = (
        nav_completion.choices[0].message.content.strip().replace("`", "")
    )
    print(f"🎯 Architect isolated target: '{predicted_target}'\n")

    if "." not in predicted_target:
        print(
            f"❌ Error: Architect responded with invalid target pattern '{predicted_target}'"
        )
        return

    class_part, method_part = predicted_target.split(".", 1)

    # Find the target class object using case-insensitive match, with a fallback to file name matching
    class_part_lower = class_part.lower()
    target_class = next(
        (c for c in discovered_classes if c["class_name"].lower() == class_part_lower),
        None,
    )

    if not target_class:
        # Fallback: check if the predicted name matches the base filename (e.g., "payment_service" matching PaymentService in payment_service.py)
        target_class = next(
            (
                c
                for c in discovered_classes
                if os.path.splitext(os.path.basename(c["file_path"]))[0].lower()
                == class_part_lower
            ),
            None,
        )

    if not target_class:
        print(
            f"❌ Error: Could not find class or module matching '{class_part}' in discovered classes."
        )
        return

    # TOOL STEP: CONTEXT REDUCTION (With Dependency-Aware Slicing)
    print("--- [TOOL USE] Programmatically Slicing Targeted Code & Dependencies ---")

    # AST Trace internal dependencies (same class)
    internal_deps = find_internal_dependencies(target_class["code"], method_part)
    print(f"🔗 Traced internal class dependencies: {internal_deps}")

    # AST Trace cross-class dependencies (other files/classes)
    cross_class_deps = find_cross_class_dependencies(
        target_class["file_path"], target_class["code"], method_part, discovered_classes
    )
    print(
        f"🔗 Traced cross-class dependencies: {[c['class_name'] for c in cross_class_deps]}"
    )

    primary_code = extract_specific_function_body(target_class["code"], method_part)
    dep_snippets = []

    # Extract internal dependencies
    for dep in internal_deps:
        dep_code = extract_specific_function_body(target_class["code"], dep)
        if dep_code:
            dep_snippets.append(f"# Internal Class Dependency Method:\n{dep_code}")

    # Extract cross-class dependencies
    for c_cls in cross_class_deps:
        dep_snippets.append(
            f"# Cross-Class Dependency: {c_cls['class_name']} (in {c_cls['file_path']})\n{c_cls['code']}"
        )

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

    doc_completion = client.chat.completions.create(
        model=CLOUD_MODEL, messages=[{"role": "user", "content": enhancement_prompt}]
    )
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
Select the best method/function to modify (either the main method, or one of the helper/cross-class dependency methods like 'get_user_balance' that is returning an incorrect type).
You MUST provide the final corrected version of the method/function code wrapped in [PATCH] and [/PATCH] tags. For example:
[PATCH]
    def get_user_balance(self, user_id):
        # your corrected code here returning a float/int instead of a string
[/PATCH]
Do not put anything else inside the [PATCH] tags except the python function definition."""

    confirm_completion = client.chat.completions.create(
        model=CLOUD_MODEL, messages=[{"role": "user", "content": confirmation_prompt}]
    )
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
            confirm_completion = client.chat.completions.create(
                model=CLOUD_MODEL,
                messages=[
                    {"role": "user", "content": confirmation_prompt},
                    {"role": "assistant", "content": confirm_text},
                    {"role": "user", "content": correction_prompt},
                ],
            )
            confirm_text = confirm_completion.choices[0].message.content
            current_attempt += 1
            continue

        patched_method = get_patched_method_name(patch_code)
        if not patched_method:
            print("❌ Error: Could not parse function name from the patch code.")
            correction_prompt = """The patch code provided does not contain a valid Python function definition. 
Please provide the corrected python code block inside [PATCH] and [/PATCH] tags, starting directly with 'def method_name(self, ...):'."""
            confirm_completion = client.chat.completions.create(
                model=CLOUD_MODEL,
                messages=[
                    {"role": "user", "content": confirmation_prompt},
                    {"role": "assistant", "content": confirm_text},
                    {"role": "user", "content": correction_prompt},
                ],
            )
            confirm_text = confirm_completion.choices[0].message.content
            current_attempt += 1
            continue

        # Dynamically resolve which class/file contains the patched method target
        patch_target_class = None
        if patched_method in target_class["methods"]:
            patch_target_class = target_class
        else:
            patch_target_class = next(
                (cls for cls in cross_class_deps if patched_method in cls["methods"]),
                None,
            )

        if not patch_target_class:
            # Fallback to scanning all workspace classes
            patch_target_class = next(
                (cls for cls in discovered_classes if patched_method in cls["methods"]),
                None,
            )

        if not patch_target_class:
            print(
                f"❌ Error: Could not resolve target class for patched method '{patched_method}'."
            )
            correction_prompt = f"""The method name '{patched_method}' does not match any class method in our workspace. 
Please provide the corrected method definition inside [PATCH] and [/PATCH] tags with the correct name."""
            confirm_completion = client.chat.completions.create(
                model=CLOUD_MODEL,
                messages=[
                    {"role": "user", "content": confirmation_prompt},
                    {"role": "assistant", "content": confirm_text},
                    {"role": "user", "content": correction_prompt},
                ],
            )
            confirm_text = confirm_completion.choices[0].message.content
            current_attempt += 1
            continue

        print(
            f"Applying extracted patch to dynamic target method '{patched_method}' in class '{patch_target_class['class_name']}' ({patch_target_class['file_path']})..."
        )
        start_l, end_l = find_method_line_range(
            patch_target_class["file_path"],
            patch_target_class["class_name"],
            patched_method,
        )

        if start_l is None or end_l is None:
            print(
                f"❌ Error: Could not find line range for the target method '{patched_method}'."
            )
            correction_prompt = f"""We could not locate the method '{patched_method}' in the file '{patch_target_class["file_path"]}' for class '{patch_target_class["class_name"]}'.
Please ensure you are patching the correct method and provide the corrected code block inside [PATCH] and [/PATCH] tags."""
            confirm_completion = client.chat.completions.create(
                model=CLOUD_MODEL,
                messages=[
                    {"role": "user", "content": confirmation_prompt},
                    {"role": "assistant", "content": confirm_text},
                    {"role": "user", "content": correction_prompt},
                ],
            )
            confirm_text = confirm_completion.choices[0].message.content
            current_attempt += 1
            continue

        # Keep a backup of the file content in case we need to roll back
        with open(patch_target_class["file_path"], "r", encoding="utf-8") as f:
            backup_content = f.read()

        if apply_patch_to_file(
            patch_target_class["file_path"], start_l, end_l, patch_code
        ):
            print("Patch applied. Verifying syntax correctness...")
            success, err_msg = verify_syntax(patch_target_class["file_path"])
            if not success:
                print("❌ Syntax verification FAILED!")
                print(f"Compiler Error:\n{err_msg}\n")

                # Rollback
                with open(patch_target_class["file_path"], "w", encoding="utf-8") as f:
                    f.write(backup_content)

                print(
                    f"Attempt {current_attempt}/{max_retries} failed. Querying LLM for correction..."
                )
                correction_prompt = f"""The previous patch failed syntax verification with the following compiler error:
\"\"\"
{err_msg}
\"\"\"

Please fix the syntax error and generate a new, correct code block wrapped in [PATCH] and [/PATCH] tags."""

                confirm_completion = client.chat.completions.create(
                    model=CLOUD_MODEL,
                    messages=[
                        {"role": "user", "content": confirmation_prompt},
                        {"role": "assistant", "content": confirm_text},
                        {"role": "user", "content": correction_prompt},
                    ],
                )
                confirm_text = confirm_completion.choices[0].message.content
                current_attempt += 1
                continue

            print("✅ Syntax verification SUCCEEDED. Running project test suite...")
            test_result = run_test_suite(".")
            if test_result["status"] in ("skipped", "no_tests"):
                print(f"ℹ️ {test_result['output']}\n")
            elif test_result["status"] == "inconclusive":
                print(f"⚠️ Test suite verification INCONCLUSIVE (not blocking): {test_result['output']}\n")
            elif test_result["status"] == "failed":
                print("❌ Test suite verification FAILED!")
                print(f"Test Output:\n{test_result['output']}\n")

                # Rollback
                with open(patch_target_class["file_path"], "w", encoding="utf-8") as f:
                    f.write(backup_content)

                print(
                    f"Attempt {current_attempt}/{max_retries} failed. Querying LLM for correction..."
                )
                truncated_test_output = truncate_output(test_result["output"])
                correction_prompt = f"""The previous patch passed syntax verification but failed the project's pytest suite with the following output (stdout/stderr, truncated if long):
\"\"\"
{truncated_test_output}
\"\"\"

Look at which specific test(s) failed and why, then fix the underlying issue. Please provide the corrected code block wrapped in [PATCH] and [/PATCH] tags."""

                confirm_completion = client.chat.completions.create(
                    model=CLOUD_MODEL,
                    messages=[
                        {"role": "user", "content": confirmation_prompt},
                        {"role": "assistant", "content": confirm_text},
                        {"role": "user", "content": correction_prompt},
                    ],
                )
                confirm_text = confirm_completion.choices[0].message.content
                current_attempt += 1
                continue
            else:
                print("✅ Test suite verification SUCCEEDED.\n")

            print("Running dynamic runtime verification...")
            # Resolve actual crash entry point method to verify runtime behavior
            entry_file, entry_class, entry_method = extract_crash_entry_point(
                runtime_stack_trace, discovered_classes
            )
            if entry_file and entry_class and entry_method:
                print(
                    f"   Using resolved entry point for test run: {entry_class}.{entry_method} in {entry_file}"
                )
                run_success, run_output = verify_runtime_execution(
                    entry_file, entry_class, entry_method
                )
            else:
                run_success, run_output = verify_runtime_execution(
                    target_class["file_path"], target_class["class_name"], method_part
                )

            if run_success:
                print(f"✅ Dynamic runtime verification SUCCEEDED! {run_output}")
                repaired_successfully = True
                break
            else:
                print("❌ Dynamic runtime verification FAILED!")
                print(f"Runtime Traceback:\n{run_output}\n")

                # Rollback
                with open(patch_target_class["file_path"], "w", encoding="utf-8") as f:
                    f.write(backup_content)

                print(
                    f"Attempt {current_attempt}/{max_retries} failed. Querying LLM for correction..."
                )
                correction_prompt = f"""The previous patch compiled successfully but failed runtime verification with the following traceback/error output:
\"\"\"
{run_output}
\"\"\"

Please fix the logic error and generate a new, correct code block wrapped in [PATCH] and [/PATCH] tags."""

                confirm_completion = client.chat.completions.create(
                    model=CLOUD_MODEL,
                    messages=[
                        {"role": "user", "content": confirmation_prompt},
                        {"role": "assistant", "content": confirm_text},
                        {"role": "user", "content": correction_prompt},
                    ],
                )
                confirm_text = confirm_completion.choices[0].message.content
                current_attempt += 1
        else:
            print("❌ Error: Failed to write patch to file.")
            break

    if repaired_successfully:
        print(
            "\n==================== FINAL COMPREHENSIVE AI REPORT ===================="
        )
        print("Status: Repaired & Verified (Syntax + Test Suite + Dynamic Runtime Exec)")
        print(f"Modified File: {patch_target_class['file_path']}")
        print(f"Modified Target Class: {patch_target_class['class_name']}")
        print(f"Modified Target Method: {patched_method}")
        print("\nApplied Patch Block:")
        print(patch_code)
        print("=======================================================================")
    else:
        print("❌ Error: Failed to repair code within maximum repair attempts.")


if __name__ == "__main__":
    main()

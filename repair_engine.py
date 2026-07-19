# repair_engine.py
import os
import re
import sys
import shutil
import subprocess
import ast
import textwrap
import importlib.util

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

def verify_runtime_execution(file_path, class_name, method_name):
    """Generates and executes a dynamic test script to verify that the patched method runs without crashing."""
    module_name = os.path.splitext(os.path.basename(file_path))[0]
    
    test_script_content = f"""
import sys
try:
    from {module_name} import {class_name}
    obj = {class_name}()
    result = getattr(obj, '{method_name}')()
    print(f"VERIFICATION_SUCCESS: Returned {{result}}")
except Exception as e:
    import traceback
    traceback.print_exc()
    sys.exit(1)
"""
    # Write temp test script
    temp_test_path = "temp_test_runner.py"
    with open(temp_test_path, 'w', encoding='utf-8') as f:
        f.write(test_script_content)
        
    try:
        result = subprocess.run(
            [sys.executable, temp_test_path],
            capture_output=True,
            text=True
        )
        # Clean up temp test script
        if os.path.exists(temp_test_path):
            os.remove(temp_test_path)
            
        if result.returncode == 0:
            return True, result.stdout.strip()
        else:
            # Combine stdout and stderr for the full error report
            err_output = (result.stdout + "\n" + result.stderr).strip()
            return False, err_output
    except Exception as e:
        if os.path.exists(temp_test_path):
            os.remove(temp_test_path)
        return False, str(e)

def _pytest_available():
    """Checks whether the pytest executable/module is available in the current environment."""
    return shutil.which("pytest") is not None or importlib.util.find_spec("pytest") is not None

def _discover_test_suite(workspace_root):
    """Walks the workspace looking for a tests/ directory, test_*.py / *_test.py files, or conftest.py."""
    for root, dirs, files in os.walk(workspace_root):
        dirs[:] = [d for d in dirs if d not in ('venv', '.git', '__pycache__')]
        if os.path.basename(root) == 'tests':
            return True
        for f in files:
            if f == 'conftest.py' or (f.startswith('test_') and f.endswith('.py')) or f.endswith('_test.py'):
                return True
    return False

def run_test_suite(workspace_root="."):
    """Runs the project's pytest suite (if one exists) and returns a structured result dict."""
    if not _pytest_available():
        return {
            "tests_found": False,
            "passed": True,
            "output": "pytest is not installed; skipping test suite verification.",
            "returncode": 0,
        }

    if not _discover_test_suite(workspace_root):
        return {
            "tests_found": False,
            "passed": True,
            "output": "No test suite found in workspace.",
            "returncode": 0,
        }

    try:
        result = subprocess.run(
            ["pytest", "--tb=short", "-q"],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=workspace_root,
        )
        output = (result.stdout + "\n" + result.stderr).strip()
        return {
            "tests_found": True,
            "passed": result.returncode == 0,
            "output": output,
            "returncode": result.returncode,
        }
    except subprocess.TimeoutExpired:
        return {
            "tests_found": True,
            "passed": False,
            "output": "Test suite timed out after 30 seconds.",
            "returncode": -1,
        }
    except Exception as e:
        return {
            "tests_found": True,
            "passed": False,
            "output": str(e),
            "returncode": -1,
        }

def extract_patch(text):
    """Helper to extract contents between [PATCH] and [/PATCH] tags, stripping markdown fences."""
    match = re.search(r'\[PATCH\](.*?)\[/PATCH\]', text, re.DOTALL)
    if match:
        content = match.group(1).strip()
        # Clean up markdown code block fences if the LLM wrapped it inside the [PATCH] block
        if content.startswith("```"):
            lines = content.split('\n')
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip().startswith("```"):
                lines = lines[:-1]
            content = '\n'.join(lines).strip()
        # Filter out trailing invalid tags if the LLM wrote them inside the block
        content_lines = content.split('\n')
        cleaned_lines = []
        for line in content_lines:
            if line.strip().startswith("tags:") or line.strip().startswith("Do not put"):
                continue
            cleaned_lines.append(line)
        return '\n'.join(cleaned_lines).strip()
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

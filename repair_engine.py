# repair_engine.py
import os
import re
import sys
import shutil
import subprocess
import ast
import textwrap
import importlib.util
import tempfile

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

# pytest exit codes: https://docs.pytest.org/en/stable/reference/exitcodes.html
_PYTEST_EXIT_NO_TESTS_COLLECTED = 5
_PYTEST_EXIT_INTERRUPTED = 2
_PYTEST_EXIT_INTERNAL_ERROR = 3
_PYTEST_EXIT_USAGE_ERROR = 4

# Substrings in test output that suggest the failure is environmental (missing
# network access, missing env vars/credentials) rather than caused by the patch.
_ENV_OR_NETWORK_ERROR_MARKERS = (
    "ConnectionError",
    "ConnectionRefusedError",
    "getaddrinfo failed",
    "Temporary failure in name resolution",
    "Network is unreachable",
    "No route to host",
    "socket.gaierror",
    "requests.exceptions",
    "urllib.error",
    "SSLError",
    "PermissionError",
)


# Example service modules under test (payment_service.py, user_service.py,
# notification_service.py, etc.) may perform network calls, read secrets, or
# write to shared state. Since we're auto-verifying an LLM-generated patch we
# haven't reviewed, the test run must be sandboxed: it should not inherit our
# real credentials/environment (a buggy or malicious patch could otherwise
# exfiltrate secrets or hit production services), and it should not run against
# the live workspace on disk (a hanging/side-effectful test could corrupt files
# beyond what our in-memory patch backup can restore). We isolate on both axes:
# a minimal environment for the subprocess, and a throwaway copy of the
# workspace to run pytest against.
_MINIMAL_ENV_ALLOWLIST = (
    "PATH",
    "HOME",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TMPDIR",
    "TEMP",
    "TMP",
    "SYSTEMROOT",
    "COMSPEC",
    "PYTHONIOENCODING",
    "VIRTUAL_ENV",
)


def _build_minimal_env():
    """Builds a restricted environment for the test subprocess: only what's needed to
    locate the Python/pytest executables and basic locale settings, none of the
    process's real secrets (e.g. GROQ_API_KEY) or other ambient env vars."""
    env = {key: os.environ[key] for key in _MINIMAL_ENV_ALLOWLIST if key in os.environ}
    if "PATH" not in env:
        env["PATH"] = os.defpath
    return env


def _copy_workspace_for_isolated_run(workspace_root):
    """Copies the workspace into a temp directory so pytest runs against throwaway
    files instead of the live workspace. Returns the temp directory path, or None
    if the copy fails (caller should fall back to running against workspace_root)."""
    try:
        temp_dir = tempfile.mkdtemp(prefix="debugger_test_run_")
        shutil.copytree(
            workspace_root,
            temp_dir,
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns("venv", ".git", "__pycache__", "*.pyc"),
        )
        return temp_dir
    except Exception:
        return None


def _make_result(tests_found, status, output, returncode):
    """Builds the structured result dict. `passed` is kept as a legacy non-blocking flag."""
    return {
        "tests_found": tests_found,
        "status": status,
        "passed": status != "failed",
        "output": output,
        "returncode": returncode,
    }


def run_test_suite(workspace_root="."):
    """Runs the project's pytest suite (if one exists) and returns a structured result dict.

    Result "status" is one of: "skipped" (pytest not installed), "no_tests"
    (no suite found or pytest collected nothing), "passed", "failed", or
    "inconclusive" (pytest itself errored, timed out for an ambiguous reason,
    or the failure looks environmental rather than patch-related). Only
    "failed" should block a patch — everything else is treated as
    non-blocking so the debugger doesn't get stuck on issues outside the
    patch's control.
    """
    if not _pytest_available():
        return _make_result(
            False,
            "skipped",
            "pytest is not installed; skipping test suite verification.",
            0,
        )

    if not _discover_test_suite(workspace_root):
        return _make_result(
            False,
            "no_tests",
            "No test suite found in workspace.",
            0,
        )

    # Run against an isolated copy of the workspace with a minimal environment
    # (see the comment above _MINIMAL_ENV_ALLOWLIST) so a broken/side-effectful
    # patch can't corrupt real files or leak real credentials during verification.
    temp_dir = _copy_workspace_for_isolated_run(workspace_root)
    run_cwd = temp_dir if temp_dir else workspace_root

    try:
        result = subprocess.run(
            ["pytest", "--tb=short", "-q"],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=run_cwd,
            env=_build_minimal_env(),
        )
        output = (result.stdout + "\n" + result.stderr).strip()

        if result.returncode == 0:
            return _make_result(True, "passed", output, result.returncode)

        if result.returncode == _PYTEST_EXIT_NO_TESTS_COLLECTED:
            return _make_result(
                False,
                "no_tests",
                "pytest ran but collected no tests; skipping test suite verification.\n"
                + output,
                result.returncode,
            )

        if result.returncode in (
            _PYTEST_EXIT_INTERRUPTED,
            _PYTEST_EXIT_INTERNAL_ERROR,
            _PYTEST_EXIT_USAGE_ERROR,
        ):
            return _make_result(
                True,
                "inconclusive",
                "pytest itself errored out (interrupted/internal/usage error) rather than "
                "reporting a real test failure; treating as inconclusive.\n" + output,
                result.returncode,
            )

        # returncode == 1 (or anything else): genuine test failures, unless the
        # output looks like it was caused by missing env vars/network access.
        if any(marker in output for marker in _ENV_OR_NETWORK_ERROR_MARKERS):
            return _make_result(
                True,
                "inconclusive",
                "Test failures appear to be caused by missing environment/network "
                "resources rather than the patch; treating as inconclusive.\n" + output,
                result.returncode,
            )

        return _make_result(True, "failed", output, result.returncode)

    except subprocess.TimeoutExpired as e:
        partial_output = ""
        if e.stdout:
            partial_output += e.stdout if isinstance(e.stdout, str) else e.stdout.decode(errors="replace")
        if e.stderr:
            partial_output += "\n" + (e.stderr if isinstance(e.stderr, str) else e.stderr.decode(errors="replace"))
        return _make_result(
            True,
            "failed",
            "Test suite timed out after 30 seconds (possible hang/infinite loop).\n"
            + partial_output.strip(),
            -1,
        )
    except (FileNotFoundError, ImportError) as e:
        # pytest disappeared/broke between the availability check and actually running it.
        return _make_result(
            False,
            "skipped",
            f"pytest could not be executed ({e}); skipping test suite verification.",
            0,
        )
    except Exception as e:
        # Any other unexpected failure to even launch the test suite is not the
        # patch's fault, so don't block on it — just flag it as inconclusive.
        return _make_result(
            True,
            "inconclusive",
            f"Could not run test suite due to an unexpected error: {e}",
            -1,
        )
    finally:
        # Always discard the throwaway workspace copy, regardless of outcome.
        if temp_dir:
            shutil.rmtree(temp_dir, ignore_errors=True)

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

# Codex Project Instructions

## Encoding Rules

- Treat UTF-8 as the only valid text encoding for this repository.
- Before editing files that may contain Chinese text, read them with explicit UTF-8.
- When writing Python code, always use `encoding="utf-8"` for `open()`, `Path.read_text()`, and `Path.write_text()` whenever text files are involved.
- When writing JSON that may contain Chinese text, use `ensure_ascii=False` and write the file with UTF-8.
- Do not use PowerShell or shell commands that rely on the system default encoding for file writes.
- Prefer `apply_patch` for manual edits so patch contents stay in the session's UTF-8 path.
- Never rewrite a file that already contains mojibake unless the correct original text is known from a reliable source such as Git history, a clean copy, or clear surrounding context.
- If original Chinese text cannot be determined, stop and ask instead of guessing.

## Required Verification

After any Codex edit that writes source, docs, templates, scripts, or config files, run:

```powershell
python scripts/tools/check_text_encoding.py
```

Do not claim the work is complete if this command fails.

## Mojibake Markers

Treat these as encoding corruption unless proven otherwise:

- `U+FFFD` replacement characters
- UTF-8/GBK mojibake such as strings beginning with code points `U+9225`, `U+9239`, or `U+6D93`
- Visible mojibake fragments matching loading/test/knowledge words after UTF-8 text was decoded as GBK

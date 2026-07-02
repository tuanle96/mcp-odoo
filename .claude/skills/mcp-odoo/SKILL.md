```markdown
# mcp-odoo Development Patterns

> Auto-generated skill from repository analysis

## Overview
This skill teaches the core development patterns and conventions used in the `mcp-odoo` Python codebase. It covers file organization, code style, commit message standards, and testing patterns. By following these guidelines, contributors can maintain consistency and quality across the project.

## Coding Conventions

### File Naming
- Use **snake_case** for all file names.
  - Example: `user_profile.py`, `order_manager.py`

### Import Style
- Use **relative imports** within the package.
  - Example:
    ```python
    from .models import User
    from .utils import calculate_total
    ```

### Export Style
- Use **named exports** (i.e., explicitly define what is exported).
  - Example:
    ```python
    __all__ = ['User', 'OrderManager']
    ```

### Commit Messages
- Use **conventional commit** format.
- Prefix commit messages with the type, such as `fix`.
- Keep commit messages concise (average ~45 characters).
  - Example:
    ```
    fix: correct total calculation in invoice
    ```

## Workflows

### Fixing a Bug
**Trigger:** When a bug or issue is identified in the codebase  
**Command:** `/fix-bug`

1. Create a new branch for the fix.
2. Locate and resolve the bug in the code.
3. Write or update tests to cover the fix.
4. Commit changes using the `fix:` prefix.
    - Example: `fix: handle NoneType in payment processing`
5. Push the branch and open a pull request.

### Adding a New Module
**Trigger:** When adding a new feature or module  
**Command:** `/add-module`

1. Create a new Python file using snake_case naming.
2. Implement the module using relative imports as needed.
3. Define `__all__` for named exports.
4. Add or update tests in a corresponding `*.test.*` file.
5. Commit with a descriptive message.
    - Example: `feat: add inventory tracking module`
6. Push and open a pull request.

## Testing Patterns

- Test files follow the pattern: `*.test.*` (e.g., `order_manager.test.py`).
- The specific testing framework is not detected; follow existing patterns in the repository.
- Place test files alongside the modules they test or in a dedicated test directory.
- Example test file name: `user_profile.test.py`

## Commands
| Command      | Purpose                                 |
|--------------|-----------------------------------------|
| /fix-bug     | Start the bug fixing workflow           |
| /add-module  | Start the new module addition workflow  |
```

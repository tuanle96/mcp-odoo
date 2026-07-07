```markdown
# mcp-odoo Development Patterns

> Auto-generated skill from repository analysis

## Overview
This skill covers the development patterns and conventions used in the `mcp-odoo` repository, a Python-based codebase without a detected framework. You'll learn about file organization, import/export styles, commit message habits, and how to write and locate tests. This guide also provides suggested commands for common workflows.

## Coding Conventions

### File Naming
- Use **snake_case** for all file names.
  - Example: `order_processor.py`, `user_utils.py`

### Import Style
- Use **relative imports** within the package.
  - Example:
    ```python
    from .models import Order
    from .utils import calculate_total
    ```

### Export Style
- Use **named exports** (explicitly define what is exported).
  - Example:
    ```python
    __all__ = ['OrderProcessor', 'calculate_total']
    ```

### Commit Messages
- Freeform style, no enforced prefixes.
- Average length: ~69 characters.
  - Example:  
    `Fix bug in order total calculation for refunds`

## Workflows

### Adding a New Module
**Trigger:** When you need to introduce new functionality.
**Command:** `/add-module`

1. Create a new Python file using snake_case naming.
2. Implement your module logic.
3. Use relative imports to reference other modules.
4. Define `__all__` to specify exports.
5. Write corresponding tests in a file matching `*.test.*`.

### Running Tests
**Trigger:** When you want to verify code correctness.
**Command:** `/run-tests`

1. Identify test files (pattern: `*.test.*`).
2. Use the project's preferred test runner (framework unknown; check documentation or use `pytest` as a default).
3. Run tests and review results.

### Refactoring Imports
**Trigger:** When reorganizing code or resolving import errors.
**Command:** `/refactor-imports`

1. Change absolute imports to relative imports within the package.
2. Ensure all modules use the `from .module import ...` style.
3. Update `__all__` as needed.

## Testing Patterns

- Test files follow the pattern: `*.test.*` (e.g., `order_processor.test.py`).
- Testing framework is **unknown**; check for a `requirements.txt` or documentation for specifics.
- Place tests alongside or near the modules they test.
- Example test file:
  ```python
  # order_processor.test.py
  from .order_processor import OrderProcessor

  def test_order_total():
      op = OrderProcessor()
      assert op.calculate_total([10, 20]) == 30
  ```

## Commands

| Command         | Purpose                                      |
|-----------------|----------------------------------------------|
| /add-module     | Scaffold and add a new module                |
| /run-tests      | Run all test files matching `*.test.*`        |
| /refactor-imports | Convert imports to relative style           |
```

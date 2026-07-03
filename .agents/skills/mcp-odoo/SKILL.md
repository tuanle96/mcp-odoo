```markdown
# mcp-odoo Development Patterns

> Auto-generated skill from repository analysis

## Overview
This skill outlines the core development patterns, coding conventions, and workflows for the `mcp-odoo` TypeScript codebase. It covers file organization, code style, commit practices, and the process for adding new example integrations. This guide is intended to help contributors quickly align with the project's standards and streamline collaboration.

## Coding Conventions

### File Naming
- Use **PascalCase** for all file names.
  - **Example:** `OrderManager.ts`, `UserProfile.test.ts`

### Import Style
- Use **relative imports** for referencing modules within the project.
  - **Example:**
    ```typescript
    import { OrderManager } from './OrderManager';
    ```

### Export Style
- Use **named exports** for all modules.
  - **Example:**
    ```typescript
    // OrderManager.ts
    export function createOrder() { ... }
    export const ORDER_STATUS = { ... };
    ```

### Commit Messages
- Follow the **Conventional Commits** format.
- Use the `feat` prefix for new features.
  - **Example:**  
    ```
    feat: add support for multi-currency transactions in order processing
    ```

## Workflows

### Add Example Integration and Documentation
**Trigger:** When you want to demonstrate how to integrate a new tool or feature with `mcp-odoo` and provide setup guidance.  
**Command:** `/add-example-integration`

1. **Create or update an example directory** under `examples/` with the name of the integration.
   - Example: `examples/stripe-integration/`
2. **Add or update a `README.md`** in the integration directory with setup or usage instructions.
   - Example: `examples/stripe-integration/README.md`
3. **Add or update configuration files** (e.g., `settings.json`) specific to the integration.
   - Example: `examples/stripe-integration/settings.json`
4. **Optionally update `examples/README.md`** to include the new integration in the list of examples.
5. **Optionally update `CHANGELOG.md`** to document the addition.

**Example Directory Structure:**
```
examples/
  stripe-integration/
    README.md
    settings.json
  README.md
CHANGELOG.md
```

**Example `README.md` for an Integration:**
```markdown
# Stripe Integration Example

This example demonstrates how to connect mcp-odoo to Stripe.

## Setup

1. Copy `settings.json.example` to `settings.json` and fill in your Stripe credentials.
2. Run the integration script:
   ```
   npm run example:stripe-integration
   ```
```

## Testing Patterns

- **Test files** follow the pattern `*.test.*` (e.g., `OrderManager.test.ts`).
- **Testing framework** is not explicitly specified; check existing test files for structure.
- Place test files alongside the modules they test or in a dedicated test directory.
- Example test file:
  ```typescript
  // OrderManager.test.ts
  import { createOrder } from './OrderManager';

  describe('createOrder', () => {
    it('should create an order with default status', () => {
      const order = createOrder({ ... });
      expect(order.status).toBe('pending');
    });
  });
  ```

## Commands

| Command                  | Purpose                                                        |
|--------------------------|----------------------------------------------------------------|
| /add-example-integration | Scaffold a new example integration with documentation and setup |

```
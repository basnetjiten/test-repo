---
name: web-scaffolder
description: Creates the standard Next.js App Router directory structure for a new web feature. Use when a plan includes new pages, layouts, or route segments under `src/app/` or `src/components/`.
compatibility: opencode
metadata:
  layer: scaffold
  pattern: nextjs-app-router
---

# Skill: web-scaffolder

## When to Use This Skill

| Condition in Task Plan | When to Invoke |
|---|---|
| Plan creates a new route or page | Always — read Directory Structure section first |
| Plan adds a new component group | Read Component Organization section |
| Plan scope is `full_feature` | Read both sections before writing any files |
| Plan scope is `ui_only` | Read Directory Structure + Component Organization |

## Directory Structure

All feature pages live under `src/app/`. Components live under `src/components/`.

```
src/
├── app/
│   └── (authenticated)/          ← route group (no URL segment)
│       └── {feature}/
│           ├── page.tsx           ← entry — server component by default
│           ├── layout.tsx         ← optional: wraps all sub-routes
│           ├── loading.tsx        ← optional: streaming suspense fallback
│           ├── error.tsx          ← optional: error boundary ('use client')
│           └── {sub-route}/
│               └── page.tsx
├── components/
│   └── {feature}/
│       ├── {FeatureName}Form.tsx  ← form component
│       ├── {FeatureName}List.tsx  ← list component
│       └── index.ts               ← barrel export
├── lib/
│   └── {feature}/
│       ├── actions.ts             ← server actions (if using server-side mutations)
│       ├── queries.ts             ← data fetching functions
│       └── schemas.ts             ← Zod validation schemas
└── types/
    └── {feature}.ts               ← shared TypeScript types
```

## Scaffold Commands

Create the directory skeleton before writing files:
```bash
mkdir -p src/app/\(authenticated\)/{feature}
mkdir -p src/components/{feature}
mkdir -p src/lib/{feature}
```

## Page Shell Template

Server component (default — no `'use client'`):
```typescript
// src/app/(authenticated)/{feature}/page.tsx
import { Metadata } from 'next';
import { {FeatureName}View } from '@/components/{feature}';

export const metadata: Metadata = {
  title: '{Feature Name} | App',
  description: '{Feature description}',
};

export default async function {FeatureName}Page() {
  return <{FeatureName}View />;
}
```

Client-interactive component:
```typescript
// src/components/{feature}/{FeatureName}View.tsx
'use client';

export function {FeatureName}View() {
  return (
    <div className="container mx-auto px-4 py-8">
      {/* content */}
    </div>
  );
}
```

Barrel export (`src/components/{feature}/index.ts`):
```typescript
export { {FeatureName}View } from './{FeatureName}View';
export { {FeatureName}Form } from './{FeatureName}Form';
```

## File Naming Conventions

| Type | Convention | Example |
|---|---|---|
| Page/Route | `page.tsx` | `src/app/(auth)/users/page.tsx` |
| Layout | `layout.tsx` | `src/app/(auth)/users/layout.tsx` |
| Component | `PascalCase.tsx` | `UserProfileCard.tsx` |
| Hook | `use{Name}.ts` | `useUserForm.ts` |
| Schema | `schemas.ts` | `src/lib/users/schemas.ts` |
| Server action | `actions.ts` | `src/lib/users/actions.ts` |
| Query function | `queries.ts` | `src/lib/users/queries.ts` |
| Type | `{feature}.ts` | `src/types/user.ts` |

## Rules

- Route files (`page.tsx`, `layout.tsx`, `loading.tsx`, `error.tsx`) are NEVER deleted — they define the URL tree
- `error.tsx` MUST include `'use client'` directive — it is always a client component
- `loading.tsx` wraps page in Suspense automatically — no manual Suspense needed at page level
- Always add SEO metadata (`export const metadata`) on every `page.tsx`
- Use route groups `(groupName)` to apply shared layouts without adding URL segments

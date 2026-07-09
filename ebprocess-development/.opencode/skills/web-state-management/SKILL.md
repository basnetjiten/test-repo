---
name: web-state-management
description: Provides Zod schema patterns, React Hook Form integration, and TanStack Query data-fetching patterns for Next.js/React features. Use when a plan includes forms, API calls, or any validated user input.
compatibility: opencode
metadata:
  layer: presentation
  pattern: zod-rhf-tanstack
---

# Skill: web-state-management

## When to Use This Skill

| Condition in Task Plan | When to Invoke |
|---|---|
| Plan includes a form or user input | Always — read Zod + React Hook Form section |
| Plan includes API data fetching | Read TanStack Query section |
| Plan scope includes `state` layer | Read both sections |
| Plan adds query parameters or search filters | Read Zod section for URL param schemas |

## 1. Zod Schemas (`src/lib/{feature}/schemas.ts`)

Define all schemas in one file. Infer TypeScript types from Zod — never duplicate type definitions.

```typescript
import { z } from 'zod';

// Form schema — used by React Hook Form
export const createUserSchema = z.object({
  firstName: z.string().min(1, 'First name is required').max(50),
  lastName: z.string().min(1, 'Last name is required').max(50),
  email: z.string().email('Invalid email address'),
  role: z.enum(['admin', 'user', 'viewer']),
});

// Infer static TypeScript type — single source of truth
export type CreateUserInput = z.infer<typeof createUserSchema>;

// Query/filter schema — used for URL search params
export const userFiltersSchema = z.object({
  search: z.string().optional(),
  role: z.enum(['admin', 'user', 'viewer']).optional(),
  page: z.coerce.number().int().positive().default(1),
});

export type UserFilters = z.infer<typeof userFiltersSchema>;
```

## 2. React Hook Form Integration (`src/components/{feature}/{Feature}Form.tsx`)

```typescript
'use client';

import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { createUserSchema, type CreateUserInput } from '@/lib/users/schemas';

export function CreateUserForm() {
  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
    reset,
  } = useForm<CreateUserInput>({
    resolver: zodResolver(createUserSchema),
    defaultValues: {
      role: 'user',
    },
  });

  const onSubmit = async (data: CreateUserInput) => {
    // call server action or API
  };

  return (
    <form onSubmit={handleSubmit(onSubmit)} noValidate>
      <input {...register('firstName')} />
      {errors.firstName && <p role="alert">{errors.firstName.message}</p>}

      <input {...register('email')} type="email" />
      {errors.email && <p role="alert">{errors.email.message}</p>}

      <button type="submit" disabled={isSubmitting}>
        {isSubmitting ? 'Saving...' : 'Submit'}
      </button>
    </form>
  );
}
```

## 3. TanStack Query — Data Fetching

### Query key factory (`src/lib/{feature}/queries.ts`)
```typescript
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';

// Centralized query key factory — prevents typos and enables precise invalidation
export const userKeys = {
  all: ['users'] as const,
  lists: () => [...userKeys.all, 'list'] as const,
  list: (filters: UserFilters) => [...userKeys.lists(), filters] as const,
  detail: (id: string) => [...userKeys.all, 'detail', id] as const,
};

// Fetch function (plain async — no React dep)
export async function fetchUsers(filters: UserFilters) {
  const params = new URLSearchParams(/* filters */);
  const res = await fetch(`/api/users?${params}`);
  if (!res.ok) throw new Error('Failed to fetch users');
  return res.json();
}

// React Query hook
export function useUsers(filters: UserFilters) {
  return useQuery({
    queryKey: userKeys.list(filters),
    queryFn: () => fetchUsers(filters),
    staleTime: 1000 * 60 * 5, // 5 minutes
  });
}

// Mutation with cache invalidation
export function useCreateUser() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (data: CreateUserInput) => {
      const res = await fetch('/api/users', {
        method: 'POST',
        body: JSON.stringify(data),
      });
      if (!res.ok) throw new Error('Failed to create user');
      return res.json();
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: userKeys.lists() });
    },
  });
}
```

## 4. Server Actions (Next.js App Router)

For forms that do NOT need optimistic UI, prefer Server Actions over client-side fetch:

```typescript
// src/lib/{feature}/actions.ts
'use server';

import { revalidatePath } from 'next/cache';
import { createUserSchema } from './schemas';

export async function createUserAction(formData: FormData) {
  const raw = Object.fromEntries(formData);
  const parsed = createUserSchema.safeParse(raw);

  if (!parsed.success) {
    return { error: parsed.error.flatten().fieldErrors };
  }

  // persist to DB / call API
  revalidatePath('/users');
  return { success: true };
}
```

## Rules

- Always define Zod schemas **before** writing form/fetch code — schema first, type inferred
- Use `z.infer<typeof schema>` — never write a separate TypeScript `interface` that duplicates a Zod schema
- Use `zodResolver` from `@hookform/resolvers/zod` for all React Hook Form validations
- Use query key factories (object with `all`, `lists`, `list(filter)`, `detail(id)`) for all TanStack Query hooks
- Prefer Server Actions for simple create/update/delete mutations; prefer TanStack Query for reads + optimistic mutations
- Set `staleTime` on all queries — never leave it as 0 (causes waterfall refetches)
- Add `noValidate` to `<form>` elements — Zod is the validation layer, not the browser

---
name: web-state-management
description: Provides Zod schema patterns, React Hook Form integration, Apollo Client 4 GraphQL patterns, Redux Toolkit slice patterns, and menu item definitions for Next.js + MUI features. Use when a plan includes forms, GraphQL operations, Redux state, or sidebar navigation.
compatibility: opencode
metadata:
  layer: presentation
  pattern: z o d-rhf-apollo-redux
---

# Skill: web-state-management

## When to Use This Skill

| Condition in Task Plan | When to Invoke |
|---|---|
| Plan includes a form or user input | Always — read Zod + React Hook Form section |
| Plan includes Apollo GraphQL operations | Read Apollo Client + Codegen + Module Hooks sections |
| Plan adds or modifies Redux state | Read Redux Toolkit Slice section |
| Plan adds sidebar navigation items | Read Menu Items section |
| Plan uses URL query params or search filters | Read Zod section for URL param schemas |
| Plan introduces new i18n strings | Read Localization section |

## 1. Zod Schemas (`src/types/{feature}.ts` or co-located)

Define all validation schemas. Infer TypeScript types from Zod — never duplicate type definitions.

```typescript
import { z } from 'zod';

export const createProductSchema = z.object({
  name: z.string().min(1, 'Name is required').max(100),
  description: z.string().optional(),
  price: z.number().positive('Price must be positive'),
  category: z.enum(['electronics', 'clothing', 'food']),
  active: z.boolean().default(true),
});

export type CreateProductInput = z.infer<typeof createProductSchema>;

// Filter/query schema
export const productFiltersSchema = z.object({
  search: z.string().optional(),
  category: z.string().optional(),
  page: z.coerce.number().int().positive().default(1),
  limit: z.coerce.number().int().positive().default(10),
});

export type ProductFilters = z.infer<typeof productFiltersSchema>;
```

## 2. React Hook Form Integration

```tsx
'use client';

import { useForm, Controller } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { TextField, Button, MenuItem } from '@mui/material';
import { createProductSchema, type CreateProductInput } from '@/types/product';

export function ProductForm() {
  const { control, handleSubmit, formState: { errors, isSubmitting } } = useForm<CreateProductInput>({
    resolver: zodResolver(createProductSchema),
    defaultValues: { active: true },
  });

  const onSubmit = async (data: CreateProductInput) => {
    // call Apollo mutation or API
  };

  return (
    <form onSubmit={handleSubmit(onSubmit)} noValidate>
      <Controller
        name="name"
        control={control}
        render={({ field }) => (
          <TextField {...field} label="Name" error={!!errors.name} helperText={errors.name?.message} fullWidth />
        )}
      />
      <Controller
        name="category"
        control={control}
        render={({ field }) => (
          <TextField {...field} select label="Category" error={!!errors.category} helperText={errors.category?.message} fullWidth>
            <MenuItem value="electronics">Electronics</MenuItem>
            <MenuItem value="clothing">Clothing</MenuItem>
            <MenuItem value="food">Food</MenuItem>
          </TextField>
        )}
      />
      <Button type="submit" variant="contained" disabled={isSubmitting}>
        {isSubmitting ? 'Saving...' : 'Submit'}
      </Button>
    </form>
  );
}
```

## 3. Apollo Client — GraphQL Setup

### Apollo Links (`src/apollo/links/`)
```typescript
// src/apollo/links/http-link.ts
import { HttpLink } from '@apollo/client';
export const httpLink = new HttpLink({
  uri: process.env.NEXT_PUBLIC_API_ENDPOINT,
  credentials: 'include'
});

// src/apollo/links/auth-link.ts
import { setContext } from '@apollo/client/link/context';
import { getSession } from 'next-auth/react';
export const authLink = setContext(async (_, { headers }) => {
  const session = await getSession();
  const token = session?.user?.access_token;
  return { headers: { ...headers, ...(token ? { Authorization: token } : {}) } };
});
```

### Client & Server Client (`src/apollo/client.ts`)
```typescript
import { ApolloClient, ApolloLink, InMemoryCache } from '@apollo/client';
import { authLink, errorLink, httpLink } from '@/apollo/links';

export const client = new ApolloClient({
  link: ApolloLink.from([authLink, errorLink, httpLink]),
  cache: new InMemoryCache()
});

export const serverClient = new ApolloClient({
  ssrMode: true,
  link: httpLink,
  cache: new InMemoryCache()
});
```

## 4. GraphQL Operation Files & Codegen

Create `.graphql` files in `src/modules/{feature}/graphql/`:

```graphql
# src/modules/products/graphql/queries.graphql
query GetProducts($input: ProductListInput) {
  getProducts(input: $input) {
    products {
      _id
      name
      price
      category
      active
    }
    pagination {
      total
      page
      limit
    }
  }
}
```

```graphql
# src/modules/products/graphql/mutations.graphql
mutation CreateProduct($input: CreateProductInput!) {
  createProduct(input: $input) {
    _id
    name
  }
}

mutation DeleteProduct($id: ID!) {
  deleteProduct(id: $id) {
    success
    message
  }
}
```

After creating `.graphql` files, run `npm run codegen` to generate typed hooks. Import from the co-located `*.generated.ts`:

```typescript
import { useGetProductsQuery, useCreateProductMutation } from './graphql/queries.generated';
```

## 5. Module Hooks Pattern (`src/modules/{feature}/hooks/useGQL.tsx`)

```typescript
import { useQuery, useMutation } from '@apollo/client';
import { GET_PRODUCTS } from '@/modules/products/graphql/queries';
import { CREATE_PRODUCT } from '@/modules/products/graphql/mutations';

const useGQL = () => ({
  GET_PRODUCTS: (variables?: any) => useQuery(GET_PRODUCTS, {
    variables,
    fetchPolicy: 'cache-and-network'
  }),
  CREATE_PRODUCT: () => useMutation(CREATE_PRODUCT, {
    refetchQueries: [{ query: GET_PRODUCTS }]
  }),
});

export default useGQL;
```

## 6. Redux Toolkit Slice (`src/store/slices/{feature}.ts`)

```typescript
import { createSlice } from '@reduxjs/toolkit';

interface ProductState {
  selectedCategory: string | null;
  viewMode: 'grid' | 'list';
}

const initialState: ProductState = {
  selectedCategory: null,
  viewMode: 'grid',
};

const productSlice = createSlice({
  name: 'product',
  initialState,
  reducers: {
    setCategory(state, action) {
      state.selectedCategory = action.payload;
    },
    setViewMode(state, action) {
      state.viewMode = action.payload;
    },
  },
});

export default productSlice.reducer;
export const { setCategory, setViewMode } = productSlice.actions;
```

Register in `src/store/reducer.ts`:
```typescript
import productReducer from './slices/product';
const reducer = combineReducers({ ..., product: productReducer });
```

## 7. Menu Items (`src/menu-items/{feature}.tsx`)

```tsx
import { FormattedMessage } from 'react-intl';
import type { NavItemType } from 'types';

// Import icon from @/components/shared/icons or @mui/icons-material

const products: NavItemType = {
  id: 'products',
  title: <FormattedMessage id="products" />,
  icon: ProductsIcon,  // Import the appropriate icon
  type: 'group',
  url: '/products',
  children: [
    {
      id: 'product-list',
      title: <FormattedMessage id="product-list" />,
      type: 'item',
      url: '/products/list',
    },
  ],
};

export default products;
```

Register in `src/menu-items/menu-items.tsx`:
```typescript
import products from './products';
const menuItems = { items: [dashboard, products, pages] };
```

## 8. Localization (`src/utils/locales/en.json`)

Add new keys to the existing JSON locale file:
```json
{
  "products": "Products",
  "product-list": "Product List",
  "product-create": "Create Product",
  "product-name": "Product Name",
  "product-price": "Price"
}
```

Use in components:
```tsx
import { FormattedMessage } from 'react-intl';
<Typography variant="h4"><FormattedMessage id="products" /></Typography>
```

## Rules

- Always define Zod schemas **before** writing form/fetch code — schema first, type inferred
- Use `z.infer<typeof schema>` — never write a separate TypeScript `interface` that duplicates a Zod schema
- Use `zodResolver` from `@hookform/resolvers/zod` for all React Hook Form validations
- Run `npm run codegen` after creating/modifying `.graphql` files — codegen generates typed hooks
- Import GraphQL hooks from the co-located `*.generated.ts` file, NOT from the raw `.graphql` file
- Use Apollo Client 4 for GraphQL — do NOT use TanStack Query or SWR for GraphQL operations
- Use Redux Toolkit `createSlice` with Immer mutable syntax for global state
- Use `useDispatch` and `useSelector` from `src/store/index.ts` (typed versions)
- Use `<FormattedMessage id="..." />` for all user-facing strings — add keys to `en.json`
- Apply `noValidate` to `<form>` elements — Zod is the validation layer
- Do NOT use Server Actions for GraphQL — the API is handled via Apollo Client

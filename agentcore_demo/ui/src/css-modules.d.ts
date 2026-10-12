// Type declaration for CSS Module imports so `import styles from "./x.module.css"`
// type-checks under TS. Vite handles the actual CSS-Module transform at build.
declare module "*.module.css" {
  const classes: { readonly [key: string]: string };
  export default classes;
}

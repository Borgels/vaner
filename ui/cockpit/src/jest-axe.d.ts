declare module 'jest-axe' {
  export function axe(container: Element | Document): Promise<unknown>
  export function toHaveNoViolations(): unknown
}

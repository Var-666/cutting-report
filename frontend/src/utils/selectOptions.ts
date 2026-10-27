export interface SelectSlotOption { value: number; sourceValue: string | number | boolean; label: string; disabled: boolean; [key: string]: unknown }
interface SlotNode { type?: unknown; props?: { value?: string | number | boolean; disabled?: unknown; label?: string }; children?: unknown }
function textContent(value: unknown): string {
  if (value === null || value === undefined || typeof value === 'boolean') return ''
  if (typeof value === 'string' || typeof value === 'number') return String(value)
  if (Array.isArray(value)) return value.map(textContent).join('')
  return typeof value === 'object' ? textContent('children' in value ? value.children : null) : ''
}

// Keep existing option slots and value types while Naive UI owns the dropdown.
export function selectOptions(nodes: readonly unknown[], group = '', output: SelectSlotOption[] = []): SelectSlotOption[] {
  for (const node of nodes) {
    if (!node || typeof node !== 'object') continue
    const { type, props, children } = node as SlotNode
    if (type === 'option') {
      const label = textContent(children).trim()
      output.push({
        value: output.length,
        sourceValue: props?.value ?? label,
        label: group ? `${group} · ${label}` : label,
        disabled: props?.disabled === '' || Boolean(props?.disabled),
      })
    } else {
      const nested = children && typeof children === 'object' && 'default' in children && typeof children.default === 'function' ? children.default() : children
      if (Array.isArray(nested)) selectOptions(nested, type === 'optgroup' ? props?.label || '' : group, output)
    }
  }
  return output
}

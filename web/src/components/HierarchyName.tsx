export function HierarchyName({ name, depth, parent }: { name: string; depth: number; parent?: string }) {
  return <span className="hierarchyName" style={{ paddingInlineStart: `${depth * 1.25}rem` }}>
    {depth > 0 && <><span aria-hidden="true">↳ </span><span className="srOnly">Level {depth + 1}, child of {parent}. </span></>}{name}</span>;
}

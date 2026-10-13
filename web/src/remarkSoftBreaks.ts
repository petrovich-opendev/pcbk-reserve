// Плагин remark: преобразует одиночные переносы строк в mdast-узлы break (13.10 Д15а).
// Дерево обходится рекурсивно; текстовые узлы с \n (и \r\n) заменяются последовательностью text/break/text…
// Узлы code и inlineCode не трогаются (это не text). Пустые куски текста не порождаются.

// Минимальный тип mdast-узла (если типы не полностью доступны)
interface MdastNode {
  type: string;
  children?: MdastNode[];
  value?: string;
}

interface Root extends MdastNode {
  type: "root";
  children: MdastNode[];
}

export function remarkSoftBreaks() {
  return (tree: Root) => {
    visit(tree, (node: MdastNode) => {
      if (node.children) {
        const newChildren: MdastNode[] = [];
        for (const child of node.children) {
          if (child.type === "text" && typeof child.value === "string") {
            const parts = child.value.split(/\r\n|\n/);
            for (let i = 0; i < parts.length; i++) {
              if (parts[i]!.length > 0) {
                newChildren.push({ type: "text", value: parts[i] });
              }
              if (i < parts.length - 1) {
                newChildren.push({ type: "break" });
              }
            }
          } else {
            newChildren.push(child);
          }
        }
        node.children = newChildren;
      }
    });
  };
}

// Рекурсивный обход дерева
function visit(node: MdastNode, callback: (n: MdastNode) => void) {
  callback(node);
  if (node.children) {
    for (const child of node.children) {
      visit(child, callback);
    }
  }
}

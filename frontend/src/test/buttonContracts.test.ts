import ts from "typescript";
import { describe, expect, it } from "vitest";

const sourceModules = import.meta.glob<string>("../**/*.tsx", {
  eager: true,
  query: "?raw",
  import: "default"
});
const reachableButtonSources = new Map(
  Object.entries(sourceModules)
    .filter(([path]) => !path.endsWith(".test.tsx") && !path.includes("/__"))
    .map(([path, source]) => [path.replace(/^\.\.\//, "src/"), source])
);

function attribute(
  opening: ts.JsxOpeningLikeElement,
  name: string
): ts.JsxAttribute | undefined {
  return opening.attributes.properties.find(
    (item): item is ts.JsxAttribute =>
      ts.isJsxAttribute(item) && ts.isIdentifier(item.name) && item.name.text === name
  );
}

function hasRealHandler(item: ts.JsxAttribute | undefined): boolean {
  if (!item?.initializer || !ts.isJsxExpression(item.initializer)) return false;
  const expression = item.initializer.expression;
  if (!expression || expression.kind === ts.SyntaxKind.UndefinedKeyword) return false;
  if (
    (ts.isArrowFunction(expression) || ts.isFunctionExpression(expression)) &&
    ts.isBlock(expression.body)
  ) {
    return expression.body.statements.length > 0;
  }
  return true;
}

function staticAttributeValue(item: ts.JsxAttribute | undefined): string | null {
  if (!item?.initializer) return null;
  if (ts.isStringLiteral(item.initializer)) return item.initializer.text;
  return null;
}

describe("reachable button contracts", () => {
  it("gives every visible application button a real click or submit handler", () => {
    const violations: string[] = [];
    let auditedButtons = 0;

    for (const [relativePath, sourceText] of reachableButtonSources) {
      const source = ts.createSourceFile(
        relativePath,
        sourceText,
        ts.ScriptTarget.Latest,
        true,
        ts.ScriptKind.TSX
      );
      const visit = (node: ts.Node) => {
        if (ts.isJsxElement(node) && node.openingElement.tagName.getText() === "button") {
          auditedButtons += 1;
          const opening = node.openingElement;
          const line = source.getLineAndCharacterOfPosition(opening.getStart()).line + 1;
          const type = staticAttributeValue(attribute(opening, "type"));
          const wired =
            hasRealHandler(attribute(opening, "onClick")) ||
            hasRealHandler(attribute(opening, "onDoubleClick"));
          // A submit button can be declared in a reusable component and mounted
          // beneath a form by its caller, which this per-file AST cannot see.
          const submitted = type === "submit";
          if (!wired && !submitted) violations.push(`${relativePath}:${line}`);
        }
        ts.forEachChild(node, visit);
      };
      visit(source);
    }

    expect(auditedButtons).toBeGreaterThan(0);
    expect(violations, `发现没有真实处理器的按钮：\n${violations.join("\n")}`).toEqual([]);
  });
});

import ts from "typescript";
import { describe, expect, it } from "vitest";
import appShellSource from "../components/AppShell.tsx?raw";
import customApiPageSource from "../pages/CustomApiPage.tsx?raw";
import homePageSource from "../pages/HomePage.tsx?raw";
import modelsPageSource from "../pages/ModelsPage.tsx?raw";
import studioPageSource from "../pages/StudioPage.tsx?raw";

const reachableButtonSources = new Map([
  ["src/components/AppShell.tsx", appShellSource],
  ["src/pages/HomePage.tsx", homePageSource],
  ["src/pages/StudioPage.tsx", studioPageSource],
  ["src/pages/ModelsPage.tsx", modelsPageSource],
  ["src/pages/CustomApiPage.tsx", customApiPageSource]
]);

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

function hasFormSubmitHandler(node: ts.Node, opening: ts.JsxOpeningLikeElement): boolean {
  if (attribute(opening, "form")) return true;
  let parent: ts.Node | undefined = node.parent;
  while (parent) {
    if (ts.isJsxElement(parent) && parent.openingElement.tagName.getText() === "form") {
      return hasRealHandler(attribute(parent.openingElement, "onSubmit"));
    }
    parent = parent.parent;
  }
  return false;
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
          const wired = hasRealHandler(attribute(opening, "onClick"));
          const submitted = type === "submit" && hasFormSubmitHandler(node, opening);
          if (!wired && !submitted) violations.push(`${relativePath}:${line}`);
        }
        ts.forEachChild(node, visit);
      };
      visit(source);
    }

    expect(auditedButtons).toBe(106);
    expect(violations, `发现没有真实处理器的按钮：\n${violations.join("\n")}`).toEqual([]);
  });
});

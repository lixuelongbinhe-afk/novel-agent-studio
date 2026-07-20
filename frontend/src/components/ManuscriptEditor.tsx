import { useEffect, useRef } from "react";
import { EditorContent, useEditor } from "@tiptap/react";
import StarterKit from "@tiptap/starter-kit";
import { Bold, Italic, List, ListOrdered, Redo2, Undo2 } from "lucide-react";

export function ManuscriptEditor({
  value,
  placeholder,
  onChange,
  onSave
}: {
  value: string;
  placeholder: string;
  onChange: (html: string) => void;
  onSave: () => void;
}) {
  const onChangeRef = useRef(onChange);
  const onSaveRef = useRef(onSave);
  onChangeRef.current = onChange;
  onSaveRef.current = onSave;
  const editor = useEditor({
    extensions: [StarterKit],
    content: toEditorHtml(value),
    editorProps: {
      attributes: {
        class: "manuscript-editor",
        role: "textbox",
        "aria-label": "正文编辑器",
        "aria-multiline": "true",
        spellcheck: "true"
      },
      handleKeyDown: (_view, event) => {
        if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") {
          event.preventDefault();
          onSaveRef.current();
          return true;
        }
        return false;
      }
    },
    onUpdate: ({ editor: current }) => onChangeRef.current(current.getHTML())
  });

  useEffect(() => {
    if (!editor) return;
    const next = toEditorHtml(value);
    if (editor.getHTML() !== next) editor.commands.setContent(next, false);
  }, [editor, value]);

  return (
    <div className="manuscript-editor-shell">
      <div className="editor-format-toolbar" aria-label="正文格式">
        <EditorButton label="粗体" active={editor?.isActive("bold")} onClick={() => editor?.chain().focus().toggleBold().run()}>
          <Bold size={16} />
        </EditorButton>
        <EditorButton label="斜体" active={editor?.isActive("italic")} onClick={() => editor?.chain().focus().toggleItalic().run()}>
          <Italic size={16} />
        </EditorButton>
        <span className="toolbar-separator" />
        <EditorButton label="无序列表" active={editor?.isActive("bulletList")} onClick={() => editor?.chain().focus().toggleBulletList().run()}>
          <List size={16} />
        </EditorButton>
        <EditorButton label="有序列表" active={editor?.isActive("orderedList")} onClick={() => editor?.chain().focus().toggleOrderedList().run()}>
          <ListOrdered size={16} />
        </EditorButton>
        <span className="toolbar-separator" />
        <EditorButton label="撤销" disabled={!editor?.can().chain().focus().undo().run()} onClick={() => editor?.chain().focus().undo().run()}>
          <Undo2 size={16} />
        </EditorButton>
        <EditorButton label="重做" disabled={!editor?.can().chain().focus().redo().run()} onClick={() => editor?.chain().focus().redo().run()}>
          <Redo2 size={16} />
        </EditorButton>
      </div>
      <div className="editor-content-wrap">
        {editor?.isEmpty ? <span className="editor-placeholder">{placeholder}</span> : null}
        <EditorContent editor={editor} />
      </div>
    </div>
  );
}

function EditorButton({
  label,
  active = false,
  disabled = false,
  onClick,
  children
}: {
  label: string;
  active?: boolean;
  disabled?: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      className={`editor-tool ${active ? "active" : ""}`}
      type="button"
      onClick={onClick}
      disabled={disabled}
      title={label}
      aria-label={label}
    >
      {children}
    </button>
  );
}

function toEditorHtml(value: string): string {
  if (!value.trim()) return "";
  if (/^\s*</.test(value)) return value;
  return value
    .split(/\r?\n/)
    .map((line) => `<p>${escapeHtml(line) || "<br>"}</p>`)
    .join("");
}

function escapeHtml(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

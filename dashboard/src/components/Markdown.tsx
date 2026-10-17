import { Fragment, type ReactNode } from "react";

/**
 * The Markdown runbooks are written in, rendered as React elements (never as HTML: nothing in a
 * runbook can run in the page). What runbooks need and no more: headings, paragraphs, lists,
 * fenced code, quotes, rules, and inline code, bold, emphasis and http(s) links.
 */

export type Block =
  | { kind: "heading"; level: number; text: string }
  | { kind: "paragraph"; text: string }
  | { kind: "list"; ordered: boolean; start: number; items: string[] }
  | { kind: "code"; text: string }
  | { kind: "quote"; text: string }
  | { kind: "rule" };

const HEADING = /^(#{1,4})\s+(.*)$/;
const BULLET = /^\s*[-*]\s+(.*)$/;
const NUMBERED = /^\s*(\d+)[.)]\s+(.*)$/;
const FENCE = /^\s*(```|~~~)/;

export function parseMarkdown(source: string): Block[] {
  const lines = source.replace(/\r\n?/g, "\n").split("\n");
  const at = (k: number) => lines[k] ?? "";
  const blocks: Block[] = [];
  let i = 0;
  while (i < lines.length) {
    const line = at(i);
    if (!line.trim()) {
      i++;
      continue;
    }
    const fence = FENCE.exec(line);
    if (fence) {
      const indent = line.length - line.trimStart().length;
      const body: string[] = [];
      i++;
      const close = fence[1] ?? "```";
      while (i < lines.length && !at(i).trimStart().startsWith(close)) {
        body.push(at(i).slice(Math.min(indent, at(i).length - at(i).trimStart().length)));
        i++;
      }
      i++; // the closing fence
      blocks.push({ kind: "code", text: body.join("\n") });
      continue;
    }
    const heading = HEADING.exec(line);
    if (heading) {
      blocks.push({ kind: "heading", level: (heading[1] ?? "#").length, text: (heading[2] ?? "").trim() });
      i++;
      continue;
    }
    if (/^\s*(---+|\*\*\*+)\s*$/.test(line)) {
      blocks.push({ kind: "rule" });
      i++;
      continue;
    }
    if (line.trimStart().startsWith(">")) {
      const body: string[] = [];
      while (i < lines.length && at(i).trimStart().startsWith(">")) {
        body.push(at(i).trimStart().replace(/^>\s?/, ""));
        i++;
      }
      blocks.push({ kind: "quote", text: body.join(" ") });
      continue;
    }
    const numbered = NUMBERED.exec(line);
    if (numbered || BULLET.test(line)) {
      const ordered = !!numbered;
      const items: string[] = [];
      while (i < lines.length) {
        const item = ordered ? NUMBERED.exec(at(i)) : BULLET.exec(at(i));
        if (item) {
          items.push(((ordered ? item[2] : item[1]) ?? "").trim());
        } else if (items.length && /^\s+\S/.test(at(i)) && !FENCE.test(at(i))) {
          items[items.length - 1] += ` ${at(i).trim()}`; // the item goes on, indented
        } else {
          break;
        }
        i++;
      }
      blocks.push({ kind: "list", ordered, start: numbered ? Number(numbered[1]) : 1, items });
      continue;
    }
    const body: string[] = [];
    while (
      i < lines.length &&
      at(i).trim() &&
      !FENCE.test(at(i)) &&
      !HEADING.test(at(i)) &&
      !BULLET.test(at(i)) &&
      !NUMBERED.test(at(i)) &&
      !at(i).trimStart().startsWith(">")
    ) {
      body.push(at(i).trim());
      i++;
    }
    blocks.push({ kind: "paragraph", text: body.join(" ") });
  }
  return blocks;
}

const INLINE = /(`[^`]+`)|(\*\*[^*]+\*\*)|(\[[^\]]+\]\([^)\s]+\))|(\*[^*\s][^*]*\*|_[^_\s][^_]*_)/g;
const SAFE_LINK = /^(https?:\/\/|mailto:)/i;

export function inline(text: string): ReactNode[] {
  const out: ReactNode[] = [];
  let last = 0;
  for (const m of text.matchAll(INLINE)) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const [token] = m;
    const key = `${m.index}`;
    if (m[1]) {
      out.push(
        <code key={key} className="rounded bg-muted px-1 py-0.5 font-mono text-[0.85em]">
          {token.slice(1, -1)}
        </code>,
      );
    } else if (m[2]) {
      out.push(<strong key={key}>{inline(token.slice(2, -2))}</strong>);
    } else if (m[3]) {
      const split = token.indexOf("](");
      const label = token.slice(1, split);
      const href = token.slice(split + 2, -1);
      out.push(
        SAFE_LINK.test(href) ? (
          <a key={key} href={href} target="_blank" rel="noreferrer" className="text-sky-400 hover:underline">
            {label}
          </a>
        ) : (
          <Fragment key={key}>{label}</Fragment>
        ),
      );
    } else {
      out.push(<em key={key}>{token.slice(1, -1)}</em>);
    }
    last = m.index + token.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

const HEADINGS = ["text-base font-semibold", "text-sm font-semibold", "text-sm font-medium", "text-sm"];

export function Markdown({ text }: { text: string }) {
  return (
    <div className="space-y-3 text-sm leading-relaxed">
      {parseMarkdown(text).map((b, n) => {
        switch (b.kind) {
          case "heading":
            return (
              <p key={n} role="heading" aria-level={b.level} className={HEADINGS[b.level - 1]}>
                {inline(b.text)}
              </p>
            );
          case "paragraph":
            return <p key={n}>{inline(b.text)}</p>;
          case "list": {
            const items = b.items.map((item, k) => <li key={k}>{inline(item)}</li>);
            return b.ordered ? (
              <ol key={n} start={b.start} className="list-decimal space-y-1 pl-5">
                {items}
              </ol>
            ) : (
              <ul key={n} className="list-disc space-y-1 pl-5">
                {items}
              </ul>
            );
          }
          case "code":
            return (
              <pre key={n} className="overflow-x-auto rounded bg-muted p-3 font-mono text-xs">
                {b.text}
              </pre>
            );
          case "quote":
            return (
              <blockquote key={n} className="border-l-2 border-border pl-3 text-muted-fg">
                {inline(b.text)}
              </blockquote>
            );
          case "rule":
            return <hr key={n} className="border-border" />;
        }
      })}
    </div>
  );
}

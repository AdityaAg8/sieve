import type { MDXComponents } from "mdx/types";

/**
 * Every MDX element is mapped to the app's own type scale and tokens, so a docs page cannot
 * introduce a colour or a font that `lint:tokens` never sees. Markdown supplies the structure;
 * the design system supplies every pixel of it.
 */
export function useMDXComponents(components: MDXComponents): MDXComponents {
  return {
    h1: (props) => <h1 className="t-h2 text-ink-0 mt-10 first:mt-0" {...props} />,
    h2: (props) => <h2 className="t-h3 text-ink-0 mt-10" {...props} />,
    h3: (props) => <h3 className="t-ui text-ink-0 mt-8" {...props} />,
    p: (props) => <p className="t-body text-ink-1 mt-4 max-w-[68ch]" {...props} />,
    ul: (props) => <ul className="t-body text-ink-1 mt-4 flex list-disc flex-col gap-2 pl-6" {...props} />,
    ol: (props) => <ol className="t-body text-ink-1 mt-4 flex list-decimal flex-col gap-2 pl-6" {...props} />,
    li: (props) => <li className="max-w-[64ch]" {...props} />,
    a: (props) => (
      <a
        className="u-interactive text-ink-0 hover:border-ink-0 border-line-2 border-b"
        {...props}
      />
    ),
    code: (props) => <code className="t-mono text-ink-0" {...props} />,
    pre: (props) => <pre className="quote mt-4 overflow-x-auto p-3" {...props} />,
    hr: () => <hr className="border-line-1 mt-10" />,
    blockquote: (props) => (
      <blockquote className="t-body text-ink-2 border-line-2 mt-4 border-l-2 pl-4" {...props} />
    ),
    ...components,
  };
}

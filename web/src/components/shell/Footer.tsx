import { footerDestinations } from "@/lib/links";

export function Footer() {
  return (
    <footer className="border-line-1 mt-24 border-t">
      <div className="mx-auto flex max-w-[84rem] flex-col gap-6 px-4 py-10 sm:px-8 md:flex-row md:items-start md:justify-between">
        <p className="t-ui text-ink-2 max-w-[46ch]">
          Scans are deleted after 7 days. Do not submit configurations containing secrets.
        </p>

        <ul className="t-ui flex flex-wrap gap-x-6 gap-y-2">
          {footerDestinations.map((destination) => (
            <li key={destination.label}>
              {destination.href ? (
                <a
                  href={destination.href}
                  rel="noreferrer noopener"
                  target="_blank"
                  className="u-interactive text-ink-1 hover:text-ink-0 border-line-2 border-b pb-0.5"
                >
                  {destination.label}
                </a>
              ) : (
                <span className="text-ink-3">
                  {destination.label}{" "}
                  <span className="text-ink-2">— not published yet</span>
                </span>
              )}
            </li>
          ))}
        </ul>
      </div>
    </footer>
  );
}

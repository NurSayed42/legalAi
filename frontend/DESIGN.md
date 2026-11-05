# Ink & Brass

The design system for LegalAI Bangladesh. The people using it may be making a real decision about their case, so clarity, sources and restraint come before flash.

All values live in [`src/styles/tokens.css`](src/styles/tokens.css) and map through [`tailwind.config.ts`](tailwind.config.ts). Tailwind's default palette and spacing scale are replaced, so an off-system value produces no CSS. `qa/unknown-classes.mjs` catches that.

## Principles

1. **Never overstate.** A number only appears if it can carry the meaning people will read into it. See "Honest numbers" below.
2. **Gold is the accent, not the paint.** Use it for the primary action, the active state, focus and key numbers. If a screen has lots of gold, something is wrong.
3. **Every state is designed.** Empty, loading, result, error, disabled, focused. Skeletons match the layout they stand in for.
4. **Bangla first.** Copy is written in Bangla, in the polite আপনি form. English appears where the source is English: statutes, case numbers, judgement text.

## Colour

| Token | Dark | Light | Use |
|---|---|---|---|
| `--bg-0` | `#0A0B0D` | `#FAF8F3` | App background (`canvas`) |
| `--bg-1` | `#111317` | `#FFFFFF` | Cards (`surface`) |
| `--bg-2` | `#171A1F` | `#F6F3EC` | Raised: nested cards, sheets, popovers (`raised`) |
| `--bg-3` | `#1E2228` | `#EFEBE2` | Inputs and hover (`field`) |
| `--text-1` | `#F2EFE8` | `#1A1A1A` | Primary text |
| `--text-2` | `#B9B4A8` | `#4C4940` | Secondary text |
| `--text-3` | `#9B968B` | `#66625A` | Muted text, still AA on every surface |
| `--gold-500` | `#C9A14A` | `#A07C33` | Brand fill: primary button, active marks, meters |
| `--accent-text` | `#E2B95C` | `#6F5414` | Gold as text. Light mode needs its own dark value, because brand gold on paper fails AA |
| `--line-1`, `--line-2` | 6%, 10% white | 8%, 14% ink | Decorative borders |
| `--line-strong` | 34% white | 50% ink | Control borders, which need 3:1 (WCAG 1.4.11) |

Semantic colours are `--success`, `--danger`, `--warning` and `--info`, each with a `-tint` background of about 12%.

`node qa/contrast.mjs` checks every text token on every surface in both themes, and fails on anything under AA. Change a token, run it.

The document "paper" (`--paper`) is light in both themes on purpose: it stands for a printed page.

## Type

| Role | Family | Notes |
|---|---|---|
| UI and body | Inter for Latin, Hind Siliguri for Bangla | One stack. Unicode ranges pick the font per character, so mixed text just works |
| Wordmark and headings | Fraunces for Latin, Noto Serif Bengali for Bangla | Used sparingly. Fraunces has no Bangla glyphs, so the Bangla serif is a separate font |
| Citations and section numbers | JetBrains Mono | 13px, Latin only |

Sizes are `text-12` to `text-48`, fluid above 16px. **Bangla is never below 14px**, so `text-12` and `text-13` are for Latin only: keyboard keys, section numbers, file names. Bangla line height is 1.7 for body and 1.35 for headings.

Bangla copy shows Bangla numerals through `bnNumber` and `bnDigits` in `lib/format.ts`.

## Space and shape

- **Spacing:** a 4px grid: 4, 8, 12, 16, 20, 24, 32, 40, 48, 64. Component sizes such as 36, 44 and 56 sit alongside it.
- **Radius:** 8 for inputs and chips, 12 for cards, 16 for panels and sheets, 999 for pills.
- **Elevation:** subtle layered shadows, plus a 1px inner top highlight on dark surfaces.
- **Ambient:** a faint gold glow behind the hero, and 2.5% grain on desktop only, so phones aren't asked to paint it.

## Motion

Durations are 120, 180 and 260ms, with `cubic-bezier(.2,.8,.2,1)`.

- **Tabs:** cross-fade plus a 4px slide.
- **New content:** fades up.
- **Sheets:** spring from the bottom, swipe to close.
- **Buttons:** scale to 0.98 on press.

Under `prefers-reduced-motion`, the distance tokens become zero, so everything collapses to opacity. Add motion through those tokens and it inherits this.

framer-motion is only used in the bottom sheet, which is a lazy chunk. Everything else is CSS.

## Touch and focus

- Minimum touch target is 44×44. Small controls keep the hit area with `.hit-target`.
- Focus is a 2px gold ring with a 2px offset, global, for every interactive element.
- Icon-only buttons need a `label`. `IconButton` won't compile without one.

## Components

All in `src/components/ui`. Build on these before adding anything new.

`Button`, `IconButton`, `Input`, `Textarea` (auto-grow), `Select`, `Chip`, `FilterChip`, `ChipGroup`, `SegmentedControl`, `Card`, `Badge`, `Tooltip`, `Dialog`, `ConfirmDialog`, `BottomSheet`, `FilterPanel` (popover on desktop, sheet on phone), `Toast`, `Skeleton`, `EmptyState`, `ErrorState`, `Kbd`, `Disclaimer`, `CitationPill`, `SourceLink`, `Meter`, `CopyButton`.

## Honest numbers

These are product rules, not styling. Each exists because the obvious design would mislead someone about their case.

- **No win probability.** `win_rate_pct` comes from at most 4 cases picked for topic similarity, with unknown outcomes in the total. The UI shows plain counts, a won/lost/other bar, and a note when the sample is small.
- **No relevance percentage.** `score` reflects rank and recency, so a good match can read as 4%. The bar is scaled to the top result in that list, with no number.
- **Generated-document laws are marked "যাচাই করুন".** They're found from the document type alone, not from the person's details.
- **No fake progress.** The API doesn't stream, so there's a typing indicator, never invented steps like "reading judgements".
- **Status means status.** The dot reflects a real API response.
- **No date range.** The API's "1950–2025" is hard-coded and misleading: most judgements are from after 2010. The UI says that instead.
- **No Appellate Division filter.** All 8,615 judgements in the index are High Court Division.

## Untrusted text

Judgement text and LLM output are data, never markup. They're rendered as text only, and ESLint forbids `dangerouslySetInnerHTML`. Highlighting and document formatting produce data structures that become React elements. Links must be `http` or `https` (`safeUrl`).

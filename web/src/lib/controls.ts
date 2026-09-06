// One height for every inline control so they line up: 44px for a thumb, 36
// from sm up.

export const control = 'h-11 sm:h-9';

// One radius scaled with the height, so the corner cuts the same share of the
// side. Anything nested subtracts its own padding.
export const radius = 'rounded-xl sm:rounded-[10px]';

// The box for every inline text control. 16px on a phone, or iOS Safari zooms
// the page on focus.
export const box = `${control} ${radius} text-base sm:text-xs`;

const filled = `${box} border border-line-strong bg-field disabled:opacity-50`;

// The field that takes a row: a path, an address, a key. Mono, since its
// contents are copied rather than written.
export const field = `${filled} w-full px-2.5 font-mono`;

// A cell in a row of them, sized by the caller and right-aligned on the digits.
export const cell = `${filled} flex-none px-2.5 text-right font-mono`;

// A select, in prose with its native arrow.
export const picker = `${filled} w-full px-2`;

// A chip in a row of cells: a code shown rather than typed. The caller pads it.
export const chip = `${filled} flex flex-none items-center font-mono`;

// The empty box at the end of a row where the next entry is typed: dashed, so
// it reads as a place rather than a value.
export const ghost = `${box} flex-none border border-dashed border-line-strong bg-transparent px-2.5 font-mono placeholder:text-faint disabled:opacity-50`;

// The cross that removes a row. The padding is the target; the negative margin
// keeps it from pushing the row around.
export const removeButton = '-m-2 rounded p-4 text-faint hover:text-danger disabled:opacity-40';

// A note about the value held, sunken under its row.
export const noteBox = 'rounded-lg border border-line bg-sunken px-3 py-2 text-[12.5px]';

const shape = `${control} ${radius} inline-flex items-center justify-center gap-2 px-3.5 text-[13px] whitespace-nowrap transition-colors disabled:opacity-50`;

// Three weights, one shape: plain for a reversible switch, accent for what a
// panel is for, danger for what throws work away.
export const button = `${shape} border border-line-strong bg-raised font-medium active:bg-sunken`;
export const primary = `${shape} bg-accent font-semibold text-on-accent active:brightness-90`;
export const danger = `${shape} border border-danger/45 bg-danger/10 font-semibold text-danger active:bg-danger/20`;

// A button that is only its word: Discard next to Save.
export const quiet = `${shape} font-medium text-dim hover:text-fg`;

// A button that is only its glyph, as Pause is on a phone. A 44px square with
// 12px corners reads as a knocked-off box, so it goes round; the word and the
// corner come back at sm.
export const glyph = `${control} inline-flex w-11 items-center justify-center gap-2 rounded-full border border-line-strong bg-raised text-[13px] font-medium whitespace-nowrap transition-colors active:bg-sunken disabled:opacity-50 sm:w-auto sm:rounded-[10px] sm:px-3.5`;

// "Create your own Jot": a tilted note-block character like the Jot mascot.
// Everything is drawn as SVG from fixed options, so a saved avatar is just three
// ids (color, face, accessory) and nothing user-supplied is ever rendered.
// Keep these ids in step with the allowed values in backend/app/api/schemas.py.

export const AVATAR_COLORS = [
  { id: "slate", name: "Slate", body: "#476b7d", face: "#bfe6f2" },
  { id: "ocean", name: "Ocean", body: "#2f6fa3", face: "#d3e9fb" },
  { id: "mint", name: "Mint", body: "#86d1b0", face: "#17402f" },
  { id: "moss", name: "Moss", body: "#5b7f3f", face: "#e6f1d3" },
  { id: "sunny", name: "Sunny", body: "#f0c93a", face: "#4a3a05" },
  { id: "tangerine", name: "Tangerine", body: "#e8833a", face: "#4a2204" },
  { id: "coral", name: "Coral", body: "#e0614f", face: "#fff0ea" },
  { id: "berry", name: "Berry", body: "#a34a86", face: "#fde7f6" },
  { id: "grape", name: "Grape", body: "#6d58b8", face: "#ece7ff" }
];

export const AVATAR_FACES = [
  { id: "happy", name: "Happy" },
  { id: "wink", name: "Wink" },
  { id: "sleepy", name: "Sleepy" },
  { id: "surprised", name: "Surprised" },
  { id: "grin", name: "Big grin" }
];

export const AVATAR_ACCESSORIES = [
  { id: "none", name: "None", phrase: "" },
  { id: "glasses", name: "Round glasses", phrase: "round glasses" },
  { id: "cap", name: "Graduation cap", phrase: "a graduation cap" },
  { id: "headphones", name: "Headphones", phrase: "headphones" },
  { id: "party", name: "Party hat", phrase: "a party hat" },
  { id: "crown", name: "Crown", phrase: "a crown" }
];

export const DEFAULT_AVATAR = { color: "slate", face: "happy", accessory: "none" };

export function normalizeAvatar(avatar) {
  const pick = (list, value, fallback) => (list.some((item) => item.id === value) ? value : fallback);
  return {
    color: pick(AVATAR_COLORS, avatar?.color, DEFAULT_AVATAR.color),
    face: pick(AVATAR_FACES, avatar?.face, DEFAULT_AVATAR.face),
    accessory: pick(AVATAR_ACCESSORIES, avatar?.accessory, DEFAULT_AVATAR.accessory)
  };
}

export function describeAvatar(avatar) {
  const choice = normalizeAvatar(avatar);
  const color = AVATAR_COLORS.find((item) => item.id === choice.color).name;
  const face = AVATAR_FACES.find((item) => item.id === choice.face).name.toLowerCase();
  const accessory = AVATAR_ACCESSORIES.find((item) => item.id === choice.accessory);
  return `${color} Jot with a ${face} face${accessory.phrase ? `, wearing ${accessory.phrase}` : ""}`;
}

export function randomAvatar() {
  const any = (list) => list[Math.floor(Math.random() * list.length)].id;
  return { color: any(AVATAR_COLORS), face: any(AVATAR_FACES), accessory: any(AVATAR_ACCESSORIES) };
}

const INK = "#263a41";
const PAPER = "#fffdf7";
const GOLD = "#f6d94a";
const RED = "#e0614f";

// Faces are drawn in the color's own "face" ink, like the mascot's light-blue features.
// Eyes and mouth are kept apart so the live tutor can blink and talk with them.
const FACES = {
  happy: (f) => ({
    eyes: `
    <ellipse cx="44" cy="67" rx="4.4" ry="6.4" fill="${f}"/>
    <ellipse cx="76" cy="64" rx="4.4" ry="6.4" fill="${f}"/>`,
    mouth: `
    <path d="M50 81 Q60 90 70 81" fill="none" stroke="${f}" stroke-width="4.6" stroke-linecap="round"/>`
  }),
  wink: (f) => ({
    eyes: `
    <ellipse cx="44" cy="67" rx="4.4" ry="6.4" fill="${f}"/>
    <path d="M70.5 65 Q76 58.5 81.5 65" fill="none" stroke="${f}" stroke-width="4.4" stroke-linecap="round"/>`,
    mouth: `
    <path d="M50 81 Q60 90 70 81" fill="none" stroke="${f}" stroke-width="4.6" stroke-linecap="round"/>`
  }),
  sleepy: (f) => ({
    eyes: `
    <path d="M38.5 66 Q44 72 49.5 66" fill="none" stroke="${f}" stroke-width="4.4" stroke-linecap="round"/>
    <path d="M70.5 63 Q76 69 81.5 63" fill="none" stroke="${f}" stroke-width="4.4" stroke-linecap="round"/>`,
    mouth: `
    <path d="M55 82 Q60 85.5 65 82" fill="none" stroke="${f}" stroke-width="4.2" stroke-linecap="round"/>`
  }),
  surprised: (f) => ({
    eyes: `
    <circle cx="44" cy="66" r="5.6" fill="${f}"/>
    <circle cx="76" cy="63" r="5.6" fill="${f}"/>`,
    mouth: `
    <ellipse cx="60" cy="84" rx="5" ry="6.4" fill="${f}"/>`
  }),
  grin: (f) => ({
    eyes: `
    <ellipse cx="44" cy="67" rx="4.4" ry="6.4" fill="${f}"/>
    <ellipse cx="76" cy="64" rx="4.4" ry="6.4" fill="${f}"/>`,
    mouth: `
    <path d="M45 78 Q60 100 75 78 Z" fill="${f}" stroke="${f}" stroke-width="3" stroke-linejoin="round"/>`
  })
};

// Accessories get a paper-colored sticker outline so they stay readable on any
// background and on any body color, in both themes.
const STICKER = `stroke="${PAPER}" stroke-width="3" stroke-linejoin="round" stroke-linecap="round" paint-order="stroke"`;

const ACCESSORIES = {
  none: () => "",
  glasses: () => `
    <g fill="none" stroke-linecap="round">
      <circle cx="44" cy="66" r="11.5" stroke="${PAPER}" stroke-width="7"/>
      <circle cx="76" cy="63" r="11.5" stroke="${PAPER}" stroke-width="7"/>
      <path d="M55.5 64 Q60 60.5 64.5 63" stroke="${PAPER}" stroke-width="7"/>
      <circle cx="44" cy="66" r="11.5" stroke="${INK}" stroke-width="3.2" fill="rgba(255,253,247,.22)"/>
      <circle cx="76" cy="63" r="11.5" stroke="${INK}" stroke-width="3.2" fill="rgba(255,253,247,.22)"/>
      <path d="M55.5 64 Q60 60.5 64.5 63" stroke="${INK}" stroke-width="3.2"/>
    </g>`,
  cap: () => `
    <g ${STICKER}>
      <path d="M40 27 v10 q20 9 40 0 v-10 z" fill="${INK}"/>
      <path d="M60 7 L99 22 L60 37 L21 22 Z" fill="${INK}"/>
      <path d="M60 22 L91 25 V43" fill="none" stroke="${GOLD}" stroke-width="3.2"/>
      <circle cx="91" cy="45" r="3.4" fill="${GOLD}"/>
    </g>`,
  headphones: () => `
    <g ${STICKER}>
      <path d="M23 62 C23 14 97 14 97 62" fill="none" stroke="${PAPER}" stroke-width="10.5"/>
      <path d="M23 62 C23 14 97 14 97 62" fill="none" stroke="${INK}" stroke-width="5.4"/>
      <rect x="11" y="52" width="17" height="29" rx="7.5" fill="${INK}"/>
      <rect x="92" y="52" width="17" height="29" rx="7.5" fill="${INK}"/>
      <rect x="15.5" y="60" width="8" height="13" rx="4" fill="${GOLD}" stroke="none"/>
      <rect x="96.5" y="60" width="8" height="13" rx="4" fill="${GOLD}" stroke="none"/>
    </g>`,
  party: () => `
    <g transform="rotate(14 60 32)" ${STICKER}>
      <path d="M60 4 L81 34 Q60 41 39 34 Z" fill="${RED}"/>
      <path d="M50 20 L67 25.5" stroke="${PAPER}" stroke-width="3.2" fill="none"/>
      <path d="M45.5 28.5 L72 35" stroke="${PAPER}" stroke-width="3.2" fill="none"/>
      <circle cx="60" cy="6" r="4.8" fill="${GOLD}"/>
    </g>`,
  crown: () => `
    <g ${STICKER}>
      <path d="M31 33 L35 10 L49 23 L60 6 L71 23 L85 10 L89 33 Z" fill="${GOLD}"/>
      <path d="M32.5 28 H87.5" stroke="#d9a91f" stroke-width="3" fill="none"/>
      <circle cx="35" cy="10" r="3.2" fill="${RED}" stroke="none"/>
      <circle cx="60" cy="6" r="3.2" fill="${RED}" stroke="none"/>
      <circle cx="85" cy="10" r="3.2" fill="${RED}" stroke="none"/>
    </g>`
};

// The whole character is drawn upright, then tilted as one piece, like the mascot.
// With `animated`, the parts get class names the live tutor's CSS can move: the
// figure breathes and bounces, the eyes blink, and a separate talking mouth takes
// over from the resting one while the tutor speaks.
export function avatarSvg(avatar, { size = 96, label = "", animated = false } = {}) {
  const choice = normalizeAvatar(avatar);
  const color = AVATAR_COLORS.find((item) => item.id === choice.color);
  const a11y = label ? `role="img" aria-label="${label}"` : 'aria-hidden="true"';
  const face = FACES[choice.face](color.face);

  const features = animated
    ? `<g class="jot-eyes">${face.eyes}</g>
      <g class="jot-mouth">${face.mouth}</g>
      <ellipse class="jot-talk" cx="60" cy="84" rx="7" ry="7.5" fill="${color.face}"/>`
    : face.eyes + face.mouth;

  const character = `<g transform="rotate(-8 60 64)">
      <rect x="18" y="26" width="84" height="76" rx="28" fill="${color.body}"/>
      <ellipse cx="38" cy="46" rx="9" ry="5.5" fill="rgba(255,255,255,.38)" transform="rotate(-38 38 46)"/>
      ${features}
      ${ACCESSORIES[choice.accessory]()}
    </g>`;

  return `<svg class="avatar${animated ? " avatar-animated" : ""}" viewBox="0 0 120 120" width="${size}" height="${size}" ${a11y} focusable="false">
    ${animated ? `<g class="jot-figure">${character}</g>` : character}
  </svg>`;
}

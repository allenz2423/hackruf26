// Fixed, reviewed feature descriptions for the bounded animal-cell activity.
// Marker coordinates are illustrative placement anchors, not scientific measurements.
export const ANIMAL_CELL_LIMITATION = "Schematic animal-cell view; shapes and positions are illustrative and not to scale. It is not a micrograph and does not show relative size, abundance, or exact placement.";

export const ANIMAL_CELL_ORGANELLES = Object.freeze({
  cytoplasm: {
    name: "Cytoplasm",
    description: "A jelly-like fluid called cytosol and other structures surround the nucleus.",
    x: 24, y: 66
  },
  cytoskeleton: {
    name: "Cytoskeleton",
    description: "A network of long fibers forms the cell's structural framework. It helps determine cell shape, participates in cell division, allows cells to move, and provides tracks that direct movement of organelles and other substances.",
    x: 67, y: 67
  },
  endoplasmic_reticulum: {
    name: "Endoplasmic reticulum (ER)",
    description: "The ER helps process molecules created by the cell and transports them to specific destinations inside or outside the cell.",
    x: 65, y: 42
  },
  golgi_apparatus: {
    name: "Golgi apparatus",
    description: "The Golgi packages molecules processed by the ER for transport out of the cell.",
    x: 40, y: 73
  },
  lysosomes_peroxisomes: {
    name: "Lysosomes and peroxisomes",
    description: "They digest foreign bacteria, remove toxic substances, and recycle worn-out cell components.",
    x: 77, y: 55
  },
  mitochondria: {
    name: "Mitochondria",
    description: "Mitochondria convert energy from food into a form the cell can use. They have their own genetic material, separate from DNA in the nucleus, and can make copies of themselves.",
    x: 28, y: 42
  },
  nucleus: {
    name: "Nucleus and nuclear envelope",
    description: "The nucleus houses DNA and sends directions for cell activities. The nuclear envelope protects DNA and separates the nucleus from the rest of the cell.",
    x: 58, y: 45
  },
  plasma_membrane: {
    name: "Plasma membrane",
    description: "The plasma membrane is the cell's outer lining. It separates the cell from its environment and allows materials to enter and leave.",
    x: 88, y: 50
  },
  ribosomes: {
    name: "Ribosomes",
    description: "Ribosomes process the cell's genetic instructions to create proteins. They can float freely in the cytoplasm or be connected to the ER.",
    x: 71, y: 27
  }
});

export function animalCellFallback(focusIds) {
  const details = focusIds.map((id) => {
    const item = ANIMAL_CELL_ORGANELLES[id];
    return `${item.name}: ${item.description}`;
  });
  return `${ANIMAL_CELL_LIMITATION} ${details.join(" ")}`;
}

export function animalCellAltText(focusIds) {
  const names = focusIds.map((id) => ANIMAL_CELL_ORGANELLES[id].name).join(", ");
  return `${ANIMAL_CELL_LIMITATION} Numbered features: ${names}.`;
}

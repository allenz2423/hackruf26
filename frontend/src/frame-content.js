// Local, bounded luminance comparisons. This data never leaves the browser.
const PIXEL_DIFFERENCE = 32;
const REGION_MARGIN = 3;

export function targetContentChanged(before, after, rect) {
  if (!before?.pixels || !after?.pixels || before.width !== after.width || before.height !== after.height) {
    return true;
  }
  const { width, height } = before;
  if (!width || !height || before.pixels.length !== width * height || after.pixels.length !== width * height) {
    return true;
  }
  const left = Math.max(0, Math.floor(rect.x * width) - REGION_MARGIN);
  const top = Math.max(0, Math.floor(rect.y * height) - REGION_MARGIN);
  const right = Math.min(width, Math.ceil((rect.x + rect.width) * width) + REGION_MARGIN);
  const bottom = Math.min(height, Math.ceil((rect.y + rect.height) * height) + REGION_MARGIN);
  const count = (right - left) * (bottom - top);
  if (count <= 0) return true;

  let changed = 0;
  let difference = 0;
  let minX = right;
  let maxX = left;
  let minY = bottom;
  let maxY = top;
  for (let y = top; y < bottom; y += 1) {
    for (let x = left; x < right; x += 1) {
      const index = y * width + x;
      const delta = Math.abs(before.pixels[index] - after.pixels[index]);
      difference += delta;
      if (delta > PIXEL_DIFFERENCE) {
        changed += 1;
        minX = Math.min(minX, x);
        maxX = Math.max(maxX, x);
        minY = Math.min(minY, y);
        maxY = Math.max(maxY, y);
      }
    }
  }
  // A narrow vertical caret blinking is not a moved target. Compare against
  // the original reference each time so slow scrolling cannot accumulate.
  const changedWidth = maxX - minX + 1;
  const changedHeight = maxY - minY + 1;
  if (changed && changedWidth <= 2 && changedHeight >= changedWidth * 2) return false;
  return changed >= 4 && ((changed / count >= 0.04 && difference / count >= 3) || difference / count >= 14);
}

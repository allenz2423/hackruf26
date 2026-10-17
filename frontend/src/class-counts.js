function validCount(value) {
  if (value === null || value === undefined) return null;
  const count = Number(value);
  return Number.isFinite(count) && count >= 0 ? count : null;
}

export function getClassDisplayCounts(klass, backendClassId, personalMaterialCounts = new Map()) {
  const uploadCount = backendClassId ? validCount(klass.uploadCount) : null;
  const sharedMaterialCount = validCount(klass.materialCount);

  return {
    uploads: uploadCount ?? (klass.uploads?.length || 0),
    materials: backendClassId
      ? (sharedMaterialCount ?? (klass.materials?.length || 0))
        + (personalMaterialCounts.get(backendClassId) || 0)
      : (klass.materials?.length || 0)
  };
}

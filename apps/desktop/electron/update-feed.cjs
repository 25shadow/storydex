function resolveUpdateFeedUrl(packageMetadata = {}, overrideUrl = "") {
  const explicitUrl = String(overrideUrl || "").trim();
  if (explicitUrl) {
    return explicitUrl;
  }

  const packagedUrl = String(packageMetadata.storydexUpdateFeedUrl || "").trim();
  if (packagedUrl) {
    return packagedUrl;
  }

  const publish = packageMetadata.build?.publish;
  const entries = Array.isArray(publish) ? publish : publish ? [publish] : [];
  const genericFeed = entries.find(
    (entry) => String(entry?.provider || "").trim() === "generic" && entry?.url
  );
  return String(genericFeed?.url || "").trim();
}

module.exports = { resolveUpdateFeedUrl };

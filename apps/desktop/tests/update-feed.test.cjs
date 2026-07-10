const assert = require("node:assert/strict");
const test = require("node:test");

const desktopPackage = require("../package.json");
const { resolveUpdateFeedUrl } = require("../electron/update-feed.cjs");

const publishedFeedUrl = desktopPackage.build.publish.find(
  (entry) => entry.provider === "generic"
).url;

test("打包后的精简元数据仍能解析默认更新源", () => {
  const packagedMetadata = {
    name: desktopPackage.name,
    version: desktopPackage.build.extraMetadata.version,
    main: desktopPackage.main,
    storydexUpdateFeedUrl: desktopPackage.build.extraMetadata.storydexUpdateFeedUrl
  };

  assert.equal(resolveUpdateFeedUrl(packagedMetadata), publishedFeedUrl);
});

test("源码元数据可回退读取 build.publish 更新源", () => {
  assert.equal(resolveUpdateFeedUrl(desktopPackage), publishedFeedUrl);
});

test("环境变量更新源优先于默认配置", () => {
  assert.equal(
    resolveUpdateFeedUrl(desktopPackage, " https://example.com/storydex/ "),
    "https://example.com/storydex/"
  );
});

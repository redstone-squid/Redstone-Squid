import { expect, test } from "@playwright/test";
import type {
  DraftMediaListResponse,
  DraftSchematicResponse,
  FormManifestResponse,
  StoredDraftResponse,
} from "../../src/generated/types.gen";

const draftId = "11111111-1111-4111-8111-111111111111";
const draftKey = "squid:submission-draft:v1";
const origin = "http://127.0.0.1:4321";
const form: FormManifestResponse = {
  schema_id: "build_submission.v1",
  minimum_protocol: 1,
  maximum_protocol: 1,
  revision: 2,
  common_sections: [],
  categories: Object.entries({
    door: "门",
    extender: "延伸器",
    utility: "实用装置",
    entrance: "入口",
    other: "其他",
  }).map(([code, label]) => ({ code, label, sections: [] })),
};
test.use({
  storageState: {
    cookies: [],
    origins: [{ origin, localStorage: [{ name: draftKey, value: draftId }] }],
  },
});

test("Chinese draft recovery preserves the draft and translates live upload progress", async ({
  page,
}) => {
  let currentRevision = form.revision;
  const draft: StoredDraftResponse = {
    id: draftId,
    schema_id: form.schema_id,
    schema_revision: form.revision - 1,
    revision: 4,
    category: "door",
    status: "editing",
    origin: "web",
    answers: {},
    created_at: "2026-11-01T00:00:00Z",
    updated_at: "2026-11-02T00:00:00Z",
    expires_at: "2026-12-01T00:00:00Z",
  };
  const schematic: DraftSchematicResponse = {
    id: "22222222-2222-4222-8222-222222222222",
    filename: "铜门.schem",
    sha256: null,
    byte_size: 512,
    primary: true,
    state: "pending",
  };
  const media: DraftMediaListResponse = {
    limits: { max_upload_bytes: 10000, max_images: 4, max_videos: 1, max_output_bytes: 20000 },
    media: [
      {
        id: "33333333-3333-4333-8333-333333333333",
        draft_id: draftId,
        kind: "video",
        status: "completed",
        source_content_type: "video/mp4",
        artifacts: [
          { role: "video_thumbnail", content_type: "image/webp", width: 400, height: 300 },
        ],
      },
    ],
  };
  await page.route("**/v1/submissions/**", async (route) => {
    const request = route.request();
    const headers = {
      "Access-Control-Allow-Origin": origin,
      "Access-Control-Allow-Credentials": "true",
      "Access-Control-Allow-Headers": "content-type, accept-language",
      "Access-Control-Allow-Methods": "GET, OPTIONS",
    };
    if (request.method() === "OPTIONS") return route.fulfill({ status: 204, headers });
    expect(request.headers()["accept-language"]).toBe("zh-CN");
    const path = new URL(request.url()).pathname;
    const draftPath = `/v1/submissions/drafts/${draftId}`;
    switch (path) {
      case "/v1/submissions/form/current":
        return route.fulfill({ json: { ...form, revision: currentRevision }, headers });
      case draftPath:
        return route.fulfill({ json: draft, headers });
      case `${draftPath}/media`:
        return route.fulfill({ json: media, headers });
      case `${draftPath}/schematics`:
        return route.fulfill({
          json: [
            schematic,
            {
              ...schematic,
              id: "44444444-4444-4444-8444-444444444444",
              filename: "旧门.schem",
              state: "rejected",
              primary: false,
            } satisfies DraftSchematicResponse,
          ],
          headers,
        });
      case `${draftPath}/schematics/${schematic.id}/suggestions`:
        return route.fulfill({
          json: { upload_id: schematic.id, state: schematic.state, draft_revision: draft.revision },
          headers,
        });
      case `${draftPath}/status`:
        return route.fulfill({ status: 404, json: { title: "尚未提交", status: 404 }, headers });
      default:
        throw new Error(`Unexpected submission request: ${path}`);
    }
  });

  await page.goto("/zh-cn/submit");
  await expect(page.getByRole("navigation", { name: "主导航" })).toBeAttached();
  const alert = page.getByRole("alert");
  await expect(alert).toContainText("当前表单无法打开这份已保存的草稿。草稿仍然保留。");
  expect(await page.evaluate((key) => localStorage.getItem(key), draftKey)).toBe(draftId);

  currentRevision = draft.schema_revision;
  await alert.getByRole("button", { name: "重试" }).click();
  await expect(page.getByText("草稿 11111111 · 修订版 4")).toBeVisible();
  const file = page.getByRole("listitem").filter({ hasText: schematic.filename });
  await expect(file).toContainText("等待分析");
  await expect(file).toContainText("主原理图");
  await expect(page.getByRole("listitem").filter({ hasText: "旧门.schem" })).toContainText(
    "未通过验证",
  );
  schematic.state = "ready";
  await expect(file).toContainText("已就绪");

  const video = page.getByRole("listitem").filter({ hasText: "视频缩略图" });
  await expect(video.getByText("视频", { exact: true })).toBeVisible();
  await expect(video).toContainText("处理完成");
  await expect(video).toContainText("视频缩略图 · 400×300 image/webp");
});

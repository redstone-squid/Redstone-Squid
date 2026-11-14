import { useState } from "react";
import { ArrowRight, Check, Plus } from "lucide-react";
import { Button } from "../../ui/react/Button";
import { Field } from "../../ui/react/Field";
import { Dialog } from "../../ui/react/Dialog";

export function Controls({ chinese }: { chinese: boolean }) {
  const [title, setTitle] = useState("");
  const [submitted, setSubmitted] = useState(false);
  const [saved, setSaved] = useState(false);
  const invalid = submitted && !title.trim();
  const t = (en: string, zh: string): string => (chinese ? zh : en);
  return (
    <div className="sq-stack">
      <div className="sq-row">
        <Dialog
          title={t("Create a draft", "创建草稿")}
          description={t(
            "Workbench example. Nothing is sent to the server.",
            "工作台示例。不会向服务器发送任何内容。",
          )}
          closeLabel={t("Close dialog", "关闭对话框")}
          trigger={
            <Button variant="primary">
              <Plus size={16} aria-hidden="true" />
              {t("New draft", "新建草稿")}
            </Button>
          }
        >
          <form
            className="sq-stack"
            noValidate
            onSubmit={(event) => {
              event.preventDefault();
              setSubmitted(true);
              setSaved(Boolean(title.trim()));
            }}
          >
            <Field
              label={t("Build title", "作品标题")}
              value={title}
              required
              onChange={(event) => {
                setTitle(event.target.value);
                setSaved(false);
              }}
              error={invalid ? t("Enter a title for your build.", "请输入作品标题。") : undefined}
            />
            <Button type="submit" variant="primary">
              {t("Save draft", "保存草稿")}
              <ArrowRight size={16} aria-hidden="true" />
            </Button>
            <p role="status">
              {saved
                ? t("Example draft saved.", "示例草稿已保存。")
                : invalid
                  ? t("Check the highlighted field.", "请检查标记的字段。")
                  : ""}
            </p>
          </form>
        </Dialog>
        <Button
          onClick={() => {
            document.getElementById("patterns")?.scrollIntoView({ behavior: "smooth" });
          }}
        >
          {t("Browse examples", "浏览示例")}
        </Button>
        <Button disabled>{t("Unavailable", "暂不可用")}</Button>
      </div>
      <div className="sq-field-grid">
        <Field
          label={t("Creator", "创作者")}
          hint={t("Public name shown on the build.", "在作品页面展示的公开名称。")}
          placeholder="CircuitSage"
        />
        <Field
          label={t("Minecraft version", "Minecraft 版本")}
          defaultValue="1.21"
          error={t("Choose Java or Bedrock as well.", "请同时选择 Java 版或基岩版。")}
        />
      </div>
      <p className="sq-inline-note">
        <Check size={16} aria-hidden="true" />
        {t(
          "Keyboard, focus, validation, and disabled states share the same primitives.",
          "键盘、焦点、验证和禁用状态使用统一的基础组件。",
        )}
      </p>
    </div>
  );
}

# 配置目录

本目录里带 `.example` 的是**模板**：第一次使用时复制成去掉 `.example` 的名字再填自己的值。

```powershell
Copy-Item 00_配置\.env.example 00_配置\.env
Copy-Item 00_配置\courses.example.json 00_配置\courses.json
```

真实的 `courses.json` / `references.json` / `page-offsets.json` / `experimental-batches.json`
与 `.env` **不入版本库**（见仓库根的 `.gitignore`）—— 它们含你的课表、书目与密钥。

# sdkmod

这个文件夹只放已经打好的 `.sdkmod`，发给别人或自己安装都从这里取。

当前文件：

| 文件 | 用途 |
| --- | --- |
| `bl1_dxvk_fix.sdkmod` | 无主之地1重制版卡顿修复（DXVK） |

## 安装

1. 先装好官方 [Willow1 SDK](https://bl-sdk.github.io/willow1-mod-db/)（重制版用 `bl1-enhanced-sdk.zip`），主菜单能看到 **MODS**。
2. 完全退出游戏。
3. 把 `bl1_dxvk_fix.sdkmod` 放到：

   `BorderlandsGOTYEnhanced\sdk_mods`

4. 启动游戏，打开 **MODS**，启用 **卡顿修复 (DXVK)**。
5. **完全退出后再开一次**。DXVK 必须在启动前就位，当次不会生效。
6. 双显卡 / 进不去游戏：选项里改「强制 NVIDIA」，并把游戏 `Binaries\Win64` 加到杀毒白名单。1.1.0 会成对安装，半套 DLL 会自动清理。

要还原：在 MODS 里关掉这个模组，再退出重开。

没有 SDK 的人请用仓库里的 exe 一键修复工具，不要只丢这个文件。

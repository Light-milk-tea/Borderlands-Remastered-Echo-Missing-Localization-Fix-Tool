# sdkmod

这个文件夹只放已经打好的 `.sdkmod`，发给别人或自己安装都从这里取。

| 文件 | 用途 |
| --- | --- |
| `echo_cn_fix.sdkmod` | Echo / 旁白字幕汉化修复 |
| `bl1_dxvk_fix.sdkmod` | 卡顿修复（DXVK） |

两个都要先装官方 [Willow1 SDK](https://bl-sdk.github.io/willow1-mod-db/)（重制版用 `bl1-enhanced-sdk.zip`），主菜单能看到 **MODS**。然后把对应 `.sdkmod` 丢进游戏目录的 `sdk_mods`，再进游戏启用。

## echo_cn_fix.sdkmod

1. 先装好天邈汉化 `BGOTYECNv1.0fix`
2. 把本文件放到 `BorderlandsGOTYEnhanced\sdk_mods`
3. 启动游戏，**MODS → Echo CN Fix**

源码和打包脚本在 `Echo_CN_Fix_SDK/`。

## bl1_dxvk_fix.sdkmod

1. 把本文件放到 `BorderlandsGOTYEnhanced\sdk_mods`
2. 启动游戏，**MODS → 卡顿修复 (DXVK)**
3. **完全退出后再开一次**。DXVK 必须在启动前就位，当次不会生效

要还原：关掉这个模组，再退出重开。

包里已经带上官方 DXVK 的 DLL，启用时不用再访问 GitHub。

# SonicStage「转换到 ATRAC3plus」的实现链路分析

> 基于本机实际安装（SonicStage 4.7 系，OpenMG 4.7.00.20061214）的静态取证，非推测。
> 环境：`C:\Program Files (x86)\Sony\SonicStage`、`C:\Program Files (x86)\Common Files\Sony Shared\`

---

## 一、结论速览

| 问题 | 结论 |
|---|---|
| 转换功能由哪个 DLL 实现？ | **不是单个 DLL**，是一套 COM 组件流水线。核心在 `omgconv2.dll` → `omgconv.dll` → `salwrap.dll` |
| 能直接逆向接口做第三方工具吗？ | **技术上可以，但成本高、收益低**。它是纯 COM（无 C 导出）、依赖 OpenMG 安全模块与 EKB 密钥、且只有 32 位 |
| 有更省事的路吗？ | 有。目标若是 `.at3`/PSP/PS3 → 用官方 `at3tool.exe`；若只要解码 → ffmpeg 已支持 ATRAC3plus |

---

## 二、组件地图（实测结果）

### 1. 转换调度层 —— `omgconv2.dll`（622 KB）

`C:\Program Files (x86)\Common Files\Sony Shared\OpenMG\omgconv2.dll`

注册为一组 COM 类（32 位视图 `HKLM\SOFTWARE\Wow6432Node\Classes\CLSID`）：

| COM 类 | CLSID | 含义 |
|---|---|---|
| **OmgPcmToOmgf** | `{A8F76733-11DE-42F2-9081-1F2E79D42FF4}` | **PCM → OMA(ATRAC3plus)，就是你要的那条路径** |
| OmgPcmToOmgfBroker | `{D4DF7678-2911-4FB0-8FAD-8AF5BCA661DD}` | 同一路径的 Broker 包装 |
| OmgAt3ToOmgf | `{5F9F8BEA-39A4-4EB7-85E4-8B5733FD3034}` | AT3 → OMA 封装 |
| OmgMsaToOmgf | `{49601784-B369-4FBD-B575-235B1B524E73}` | MSA → OMA |
| OmgEat3ToAtp | `{98462E8B-36B7-46A7-9CC9-B49E43C2F9D5}` | EA3 → ATP（ATRAC3plus 码流） |

`InprocServer32` 均指向 `omgconv2.dll`；注册脚本里引用的 LIBID 是 `{475D2290-0DB2-42DA-8E1F-A6BE0DC2A1A6}`，
但该 LIBID **在本机没有注册，且 DLL 内也没有 TYPELIB 资源** → 没有现成类型库可用，接口只能靠 vtable 反推。

### 2. 老一代转换库 —— `omgconv.dll`（225 KB，407 个导出）

`...\OpenMG\omgconv.dll` —— 这一层**有名字导出**（C++ mangled），是最接近"可直接调用"的一层：

```
??0WAVToOMGF@@QAE@XZ                 WAVToOMGF::WAVToOMGF()      ← WAV→OMA，即 ATRAC3plus 编码路径
??0CNV_Encoder@@QAE@XZ               CNV_Encoder 基类
??0CNV_Decoder@@QAE@XZ / CNV_FileIO / CNV_Format / CNV_Transform
?TRS_convertHeader@WRAP_OMGConvert   (in, len, out, outlen, &written)
?TRS_convertBlock@WRAP_OMGConvert    分块转换（流式编码主循环）
?TRS_generateMAC@WRAP_OMGConvert     生成校验 MAC
?TRS_initConvertBlock / TRS_terminateConvertBlock
?ProcessStart/ProcessOne/ProcessEnd@MSAToOMGFConv
```

`TRS_convertBlock` 的签名能从修饰名还原出来，是块式编解码循环的标准形状。

### 3. 真正的编解码核心 —— `salwrap.dll`（282 KB）

`...\OpenMG\salwrap.dll` —— 导出**只有 3 个符号**：`salwrap0` / `salwrap1` / `salwrap2`，明显是门面转发器。
字符串证据：

```
SAL INTERNAL ERROR:
ERR_MT_ATRAC
ERR_SML_SINF_TSAL_VERSION
salwrap: resource NEWEKB_TXT not found      ← 依赖 EKB（Enablement Key Block）密钥块
e:\DailyBuild\4_7_00_200612140\OpenMG\src\omg\exe\Release\salwrap2.pdb
```

SAL（Sony Audio Library）被包在 OpenMG 的密钥体系里 —— 这才是最大的拦路石，见第三节。

### 4. ATRAC3plus（ATP）相关的配套插件

| 文件 | 证据 |
|---|---|
| `AVLib\AtpFringe.dll` | COM：`ATPFringe.OmgPcATPFringe` `{8DE9B44D-4FBB-456B-8AC9-14927DE552D6}`，接口 `IOmgPcATPFringe` |
| `OpenMG\omgatpcd.dll` | COM；字符串 `ATPCDEKB`、`ATPCDEKB_V21`、`bad ATP media info file` |
| `AVLib\OpcAtp.dll` | `COmgPcAtp`，日志串 "OmgPcATPFringe CoCreateInstance fail" |
| `OpenMG\pfcom.dll` | COM + `smbImportContent()`，导入 salwrap |
| `OpenMG\OMGUtils.dll` | 269 导出，含 `Header@Atp`、SHA、锁、日志等基础设施 |
| `OpenMG\omgtrans.ax / saliconv.ax / saloconv.ax` | DirectShow 滤镜，走图（graph）方式串起编解码 |

### 5. 顺带确认：没有 ACM 入口

检查 `Drivers32` 注册表：本机只有系统自带 `msacm.*`（imaadpcm / l3acm / msadpcm / msg711 / msgsm610），
**没有任何 Sony ATRAC ACM 编解码器**。所以不存在"注册个 ACM 就能被任意软件调用"的捷径。
（RareWares 的资料也说 Sound Forge 10 那个 ACM codec 只支持 ATRAC3，不支持 ATRAC3plus。）

---

## 三、为什么"直接调 DLL"很痛苦

1. **纯 COM，没有 C API。** 所有关键组件只导出 `DllGetClassObject` / `DllRegisterServer`。
   要先拿到 CLSID → 再拿接口 IID → 再按 vtable 顺序猜方法。没有类型库的话，参数含义全靠反汇编。
2. **编码器在安全模块后面。** `salwrap.dll` 需要 EKB 密钥块（`NEWEKB_TXT`、`ATPCDEKB_V21`），
   和 OpenMG 的授权/激活状态、注册表、`OMGKEY`/`OMGRIGHT`/`ekb` 目录绑定。
   脱离 SonicStage 单独 LoadLibrary，大概率卡在密钥/授权校验，而不是卡在参数。
3. **输出是 DRM 包裹的。** 走 OpenMG 路径产出的是 `.oma`/`.omg` 加密容器，不只是裸 ATRAC3plus 码流。
   你要的如果只是码流，这条路是绕远路。
4. **全是 32 位。** `machine=0x14c` + `MSVCR71/MSVCP71` + `atl71/mfc71`，VC7.1 时代产物。
   你的工具必须编译成 x86，不能用 64 位进程加载。
5. **依赖链长且带状态。** `omgconv2 → omgconv → pfcom/salwrap/OMGUtils/omglgd`，
   外加运行中需要的全局锁、服务（`SsBeSvc.exe` 等）和临时目录，不是"拷几个 DLL 出来就能跑"。

---

## 四、如果一定要逆向，路线图

1. **抓调用链**：Process Monitor / API Monitor 挂 `omgjbox.exe` 或 SonicStage 主程序，
   过滤 `LoadLibrary` + `CreateFile`，转换一次，看实际加载了哪些模块、读写了哪些密钥文件。
2. **拿接口**：`oleview.exe`（Windows SDK）看注册的类；
   没有类型库就上 **Ghidra / IDA + x64dbg**，在 `CoCreateInstance` / `DllGetClassObject` 下断，
   顺着 vtable 把 `IOmgPcmToOmgf` 的方法枚举出来。
   更快的办法：写个 **32 位** 小程序 `CoCreateInstance(CLSID_OmgPcmToOmgf, IID_IUnknown)`，
   然后 `QueryInterface` 逐个试接口、dump vtable 指针、对着 `omgconv2.dll` 的映像基址算出每个槽位的函数地址。
3. **验证参数**：`omgconv.dll` 有名字导出，`TRS_*` 系列的参数个数/宽度已从修饰名可推断，
   可以用它当"语义锚点"去校验你在 `omgconv2` 里猜的方法。
4. **写壳**：x86 C++/C# harness，显式 `LoadLibrary` 指定绝对路径 + `CoCreateInstance`，
   自己保证 msvcr71/msvcp71/atl71 在搜索路径里。

> 提示：本次验证时，用 32 位 PowerShell 实例化 `Omgconv2.OmgPcmToOmgf` 的步骤被本机安全策略拦下了
> （"COM object instantiation can run arbitrary code"）。你在自己环境里跑下面这行就能拿到第一手结果：
> ```
> %SystemRoot%\SysWOW64\WindowsPowerShell\v1.0\powershell.exe -Command `
>   "$o = New-Object -ComObject 'Omgconv2.OmgPcmToOmgf'; $o | Get-Member -Force"
> ```

---

## 五、更划算的替代方案

| 你的目标 | 推荐做法 |
|---|---|
| PSP / PS3 / 游戏音频用的 `.at3`（RIFF 裸码流，无 DRM） | **SCEI ATRAC3plus Codec Tool `at3tool.exe`**（官方 PSP SDK 内 `psp_sdk_660/usr/local/psp/devkit/tool/at3tool`）。公认最好的 ATRAC3plus 编码器，且是索尼官方发布。Linux 版要手动装 `libatrac.so.1.2.0` 并建软链 |
| 想要图形界面 | **ATRACTool-Reloaded**（GitHub，MIT，C#）—— 就是 `at3tool`/`at9tool` 的 GUI 外壳，支持 wav/mp3/flac/ogg ↔ at3/at9 |
| 要出 Walkman 用的 `.oma/.omg` | 同上项目的 Walkman 功能，它内部正是走 OpenMG Library（需装 Sony Media Library Earth）—— 等于别人已经替你把 COM 调好了 |
| 只要解码 ATRAC3plus | **ffmpeg**（`atrac3plus` 解码器 2013 年进主线，Maxim Poliakovski 逆向实现，支持 `.at3`/`.oma`/`.aa3`） |
| MiniDisc / NetMD / Hi-MD | **atracdenc**（FOSS，ATRAC1/ATRAC3 编码）+ **Web MiniDisc Pro** / linux-minidisc 传输 |
| 只要 ATRAC9 | `at9tool.exe` + ATRACTool-Reloaded |

---

## 六、法律与合规提醒（不是法律意见）

- SonicStage / OpenMG 的 EULA 通常明确禁止反向工程、反汇编，以及**再分发**其中的 DLL。
  自用做互操作与公开发布工具，风险量级完全不同。
- OpenMG 同时是 DRM 系统。绕过它的加密/授权属于另一性质的问题，风险明显高于"调用编码器"本身，不建议碰。
- 比较法上：欧盟允许为"互操作性"目的的逆向；美国 DMCA §1201(f) 有有限的互操作例外；
  中国《计算机软件保护条例》对"研究软件设计思想/原理"的逆向留了空间，但禁止复制性使用与对外提供。
  真要发布工具，建议先咨询专业法律意见。
- 结论：**自己在本机研究没问题；别把 Sony 的 DLL 打进你的发行包。**

---

## 附：本次取证用的脚本（可复用）

- `pe_dump.py` —— 纯 Python 解析 PE 导出表 / 导入表（无需 binutils）
- `res_extract.py` —— 提取 PE 资源（本次用它确认 omgconv2.dll 内**没有** TYPELIB 资源）

用法：

```bash
python pe_dump.py "C:/Program Files (x86)/Common Files/Sony Shared/OpenMG/omgconv.dll"
python res_extract.py "<dll路径>" out.tlb
```

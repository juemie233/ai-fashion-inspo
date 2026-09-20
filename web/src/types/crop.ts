/** 手机图剪裁（AdminPhoneCrop）跨组件复用的类型。
 *
 * 候选网格与执行结果拆到 `components/admin/AdminCropResultsPanel.vue` 后，
 * 这些接口被父子两侧同时引用，故按项目约定收敛到 types/ 下（与 cropResult.ts
 * 的「执行结果」类型分开放：这里只放**扫描候选**及其展示枚举）。
 */

/** 扫描候选（后端 /admin/crop-phone-screenshots/scan 返回的一项） */
export interface CropCandidate {
  id: string
  file_path: string
  width: number
  height: number
  ratio: number
  crop_top: number
  crop_bottom: number
  auto_ok: boolean
  note: string | null
  confidence: 'high' | 'medium' | 'low'
  /** content 模式：gray_band（灰带包夹）/ status_bar（状态栏+播放器条）/ plain / glyph_only（字形证据，行剖面无信号） */
  boundary_kind?: 'gray_band' | 'status_bar' | 'plain' | 'glyph_only' | null
  /** 后端勾选决策：字形证据（左右两角齐备）的残留候选默认勾选，无字形证据的不勾。
   * 旧响应无此字段时回退到「带建议比例即勾选」的兼容推断 */
  auto_checked?: boolean | null
  /** AI 复核结果（三态）：true=阳性（检出 UI 残留，置顶标注）/ false=阴性
   * （已从候选移除，不会再出现）/ null=未知（判定失败/超时，保守保留待人工） */
  vlm_residue?: boolean | null
  created_at: string | null
}

/** 候选网格密度（紧凑/标准/宽松），偏好持久化，与素材库页面行为一致 */
export type CropGridDensity = 'compact' | 'standard' | 'comfortable'

<script setup lang="ts">
/** 负样本初筛器卡片：训练状态、指标、正负样本统计与训练/重置操作。 */

import { computed, onMounted } from 'vue'
import { useQualityLearner } from '@/composables/useQualityLearner'

const { status, loading, training, resetting, loadStatus, train, reset } = useQualityLearner()

/** 样本是否不足（正负至少各 1 条含向量样本 + 总量 ≥ 10 才能训练） */
const canTrain = computed(() => {
  const d = status.value?.dataset
  if (!d || d.error) return false
  return (
    (d.positive_with_vector ?? 0) >= 1 &&
    (d.negative_with_vector ?? 0) >= 1 &&
    (d.with_vector ?? 0) >= 10
  )
})

onMounted(loadStatus)
</script>

<template>
  <a-card size="small" title="负样本初筛器" style="margin-bottom: 16px">
    <template #extra>
      <a-button size="mini" @click="loadStatus" :loading="loading">刷新</a-button>
    </template>

    <a-spin :loading="loading">
      <template v-if="status">
        <!-- 状态行 -->
        <div
          style="display: flex; align-items: center; gap: 12px; flex-wrap: wrap; margin-bottom: 8px"
        >
          <a-tag :color="status.trained ? 'green' : 'gray'" size="small">
            {{ status.trained ? '已训练' : '未训练' }}
          </a-tag>
          <span style="font-size: 12px; color: #666">
            自动拒绝阈值 {{ status.threshold }}（低置信度仍走 VLM 复审）
          </span>
        </div>

        <!-- 数据集统计 -->
        <a-alert v-if="status.dataset?.error" type="warning" style="margin-bottom: 8px">
          {{ status.dataset.error }}
        </a-alert>
        <div v-else-if="status.dataset" style="font-size: 12px; color: #666; margin-bottom: 8px">
          样本池：正 {{ status.dataset.positive_total ?? 0 }} / 负
          {{ status.dataset.negative_total ?? 0 }}
          <span style="margin-left: 12px">
            含向量：{{ status.dataset.with_vector ?? 0 }} （正
            {{ status.dataset.positive_with_vector ?? 0 }} / 负
            {{ status.dataset.negative_with_vector ?? 0 }}）
          </span>
        </div>

        <!-- 训练指标 -->
        <a-row v-if="status.meta" :gutter="[8, 8]" style="margin-bottom: 8px">
          <a-col :flex="1"
            ><a-statistic title="准确率" :value="status.meta.metrics.accuracy"
          /></a-col>
          <a-col :flex="1"
            ><a-statistic title="精确率" :value="status.meta.metrics.precision"
          /></a-col>
          <a-col :flex="1"
            ><a-statistic title="召回率" :value="status.meta.metrics.recall"
          /></a-col>
          <a-col :flex="1"><a-statistic title="F1" :value="status.meta.metrics.f1" /></a-col>
          <a-col :flex="1">
            <a-statistic
              title="误杀率"
              :value="status.meta.metrics.false_reject_rate"
              :value-style="{
                color: status.meta.metrics.false_reject_rate > 0.05 ? '#d03050' : '#18a058',
              }"
            />
          </a-col>
        </a-row>
        <p v-if="status.meta" style="font-size: 11px; color: #999; margin: 0 0 8px">
          最近训练 {{ status.meta.trained_at?.slice(0, 16).replace('T', ' ') }} · 训练样本
          {{ status.meta.sample_total }}（正 {{ status.meta.positive }} / 负
          {{ status.meta.negative }}）· 验证集 {{ status.meta.metrics.test_size }} 条 · 混矩阵 TN
          {{ status.meta.metrics.confusion.tn }} / FP {{ status.meta.metrics.confusion.fp }} / FN
          {{ status.meta.metrics.confusion.fn }} / TP {{ status.meta.metrics.confusion.tp }}
        </p>

        <!-- 操作 -->
        <a-space align="center">
          <a-button
            type="primary"
            size="small"
            :loading="training"
            :disabled="!canTrain"
            @click="train"
          >
            {{ status.trained ? '重新训练' : '训练初筛器' }}
          </a-button>
          <a-popconfirm
            v-if="status.trained"
            content="删除已训练模型，质量审核回退到纯 VLM 判定。指标变差时可使用此操作。确定继续？"
            @ok="reset"
          >
            <a-button size="small" type="secondary" status="danger" :loading="resetting">
              重置（回滚纯 VLM）
            </a-button>
          </a-popconfirm>
          <span v-if="!canTrain" style="font-size: 12px; color: #f0a020">
            样本不足（需正负至少各 1 条、共 ≥10 条含向量样本），先积累「质量差」垃圾桶/已拒绝素材
          </span>
        </a-space>
        <p style="font-size: 11px; color: #999; margin: 8px 0 0">
          初筛器用 CLIP 图像向量训练 sklearn 分类器，高置信度垃圾直接拒绝、低置信度仍走 VLM
          复审；重置接口受 API Key 保护。
        </p>
      </template>
      <a-empty v-else description="初筛器状态加载失败">
        <a-button size="small" @click="loadStatus">重试</a-button>
      </a-empty>
    </a-spin>
  </a-card>
</template>

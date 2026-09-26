<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../api.js'

const role = ref(localStorage.getItem('role') || '')
const temps = ref([])
const err = ref('')
const lockForm = ref({ ambient_c: '' })
const editId = ref(null)
const editValue = ref('')

const SOURCE_LABELS = { job: '随单锁定', manual: '手动锁定', seed: '种子数据' }

function isBlank(v) {
  return v === '' || v === null || v === undefined || Number.isNaN(v)
}

async function refresh() {
  try {
    temps.value = await api('/api/temps')
    err.value = ''
  } catch (e) {
    err.value = String(e.message || e)
  }
}

async function lockTemp() {
  err.value = ''
  if (isBlank(lockForm.value.ambient_c)) {
    err.value = '缺温：请填写环境温度（°C）后再锁定'
    return
  }
  try {
    await api('/api/temps', {
      method: 'POST',
      body: JSON.stringify({ ambient_c: lockForm.value.ambient_c }),
    })
    lockForm.value.ambient_c = ''
    await refresh()
  } catch (e) {
    err.value = String(e.message || e)
  }
}

function startEdit(t) {
  editId.value = t.id
  editValue.value = t.ambient_c
}

async function saveEdit(id) {
  err.value = ''
  if (isBlank(editValue.value)) {
    err.value = '缺温：改温也必须填写环境温度（°C）'
    return
  }
  try {
    await api(`/api/temps/${id}`, {
      method: 'PUT',
      body: JSON.stringify({ ambient_c: editValue.value }),
    })
    editId.value = null
    await refresh()
  } catch (e) {
    err.value = String(e.message || e)
  }
}

function fmtTime(s) {
  if (!s) return '—'
  const d = new Date(s)
  return Number.isNaN(d.getTime()) ? s : d.toLocaleString()
}

onMounted(() => {
  role.value = localStorage.getItem('role') || ''
  refresh()
})
</script>

<template>
  <div>
    <h2>温感台</h2>

    <section style="margin:16px 0; padding:12px; border:1px solid #d8a000; background:#fff8e1;">
      <h3>必填说明</h3>
      <p>环境温度为<strong>必填项</strong>：提交校准时必须填写环境温度（°C），缺温将被拒收。</p>
      <p>温度<strong>随单冻结</strong>：提交后该单温度即锁定，事后修改温表不会改动已提交的旧单。</p>
      <p>巡检员可查看温度列与本清单，但不可提交。</p>
    </section>

    <p v-if="err" style="color:#b00020">{{ err }}</p>

    <section v-if="role === 'writer'" style="margin:16px 0; padding:12px; border:1px solid #ccc;">
      <h3>温栏</h3>
      <label>环境温度 °C（必填）
        <input type="number" step="0.1" v-model.number="lockForm.ambient_c" placeholder="如 24.5" required />
      </label>
      <button type="button" @click="lockTemp">锁定温度</button>
    </section>

    <h3>已锁温度清单</h3>
    <table border="1" cellpadding="6" style="border-collapse:collapse; width:100%;">
      <thead>
        <tr>
          <th>编号</th><th>环境温度（°C）</th><th>来源</th><th>关联单号</th><th>锁定人</th><th>锁定时间</th><th>改温时间</th>
          <th v-if="role === 'writer'">操作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="t in temps" :key="t.id">
          <td>{{ t.id }}</td>
          <td>
            <input
              v-if="editId === t.id"
              type="number"
              step="0.1"
              v-model.number="editValue"
              style="width:90px"
            />
            <template v-else>{{ t.ambient_c }}</template>
          </td>
          <td>{{ SOURCE_LABELS[t.source] || t.source }}</td>
          <td>{{ t.job_id ?? '—' }}</td>
          <td>{{ t.created_by }}</td>
          <td>{{ fmtTime(t.created_at) }}</td>
          <td>{{ fmtTime(t.updated_at) }}</td>
          <td v-if="role === 'writer'">
            <template v-if="editId === t.id">
              <button type="button" @click="saveEdit(t.id)">保存</button>
              <button type="button" @click="editId = null">取消</button>
            </template>
            <button v-else type="button" @click="startEdit(t)">改温</button>
          </td>
        </tr>
        <tr v-if="!temps.length">
          <td :colspan="role === 'writer' ? 8 : 7" style="text-align:center">暂无已锁温度</td>
        </tr>
      </tbody>
    </table>
    <p style="color:#666; font-size:13px">提示：改温只更新温表；已提交单据的温度随单冻结，不受影响。</p>
  </div>
</template>

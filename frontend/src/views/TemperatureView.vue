<script setup>
import { onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api.js'

const GATE_KEY = 'gate_ambient_c'

const router = useRouter()
const role = ref(localStorage.getItem('role') || '')
const isWriter = ref(role.value === 'writer')
// 温栏：校准员可登记；巡检员可见但只读、不可提交。
const tempInput = ref('')
const gateTemp = ref(sessionStorage.getItem(GATE_KEY) || '')
const jobs = ref([])
const err = ref('')
const note = ref('')
let timer

function syncFromGate() {
  gateTemp.value = sessionStorage.getItem(GATE_KEY) || ''
}

async function refresh() {
  if (!localStorage.getItem('tok')) return
  try {
    jobs.value = await api('/api/jobs')
    err.value = ''
  } catch (e) {
    err.value = String(e.message || e)
  }
}

// 登记当前环境温度，作为提交校准单的必填门禁值（随单冻结）。
function confirmTemp() {
  err.value = ''
  note.value = ''
  const v = parseFloat(tempInput.value)
  if (tempInput.value === '' || Number.isNaN(v)) {
    err.value = '缺温拒收：请先填写环境温度（℃）'
    return
  }
  sessionStorage.setItem(GATE_KEY, String(v))
  syncFromGate()
  note.value = `已登记本单环境温度 ${v} ℃，前往校准总览提交时将随单冻结。`
}

function clearGate() {
  sessionStorage.removeItem(GATE_KEY)
  syncFromGate()
  note.value = '已清除门禁温度；未重新登记前提交校准单将被拒收（缺温）。'
}

function goDetail(id) {
  router.push(`/jobs/${id}`)
}

onMounted(() => {
  role.value = localStorage.getItem('role') || ''
  isWriter.value = role.value === 'writer'
  if (gateTemp.value !== '') tempInput.value = gateTemp.value
  refresh()
  timer = setInterval(refresh, 1000)
})
onUnmounted(() => clearInterval(timer))
</script>

<template>
  <div>
    <p v-if="err" style="color:#b00020">{{ err }}</p>

    <!-- 必填门禁说明 -->
    <section style="margin:16px 0; padding:12px; border:1px solid #b00020; background:#fff6f6;">
      <h3 style="margin-top:0">环境温度必填门禁</h3>
      <ul style="margin:6px 0; padding-left:20px; line-height:1.7;">
        <li>环境温度为<strong>必填项</strong>：提交校准单前必须先在此登记环境温度。</li>
        <li><strong>缺温拒收</strong>：未填写环境温度的校准单一律拒收，且会明确提示“缺温”。</li>
        <li>温度<strong>随单冻结</strong>：提交后写入单据，事后修改温表不会改动旧单温度。</li>
        <li>巡检员可查看温度，但<strong>不可提交</strong>。</li>
      </ul>
    </section>

    <!-- 温栏 -->
    <section style="margin:16px 0; padding:12px; border:1px solid #ccc;">
      <h3 style="margin-top:0">温栏（环境温度）</h3>
      <label>
        环境温度（℃）
        <input
          type="number"
          step="0.1"
          v-model="tempInput"
          :disabled="!isWriter"
          :placeholder="isWriter ? '如 24.5' : '巡检员只读'"
        />
      </label>
      <p v-if="gateTemp !== ''" class="hint">当前门禁温度：<strong>{{ gateTemp }} ℃</strong>（提交校准时随单冻结）</p>
      <template v-if="isWriter">
        <button type="button" @click="confirmTemp">登记温度并解锁提交</button>
        <button type="button" @click="clearGate" style="margin-left:8px">清除重填</button>
        <router-link to="/" style="margin-left:12px">去校准总览提交 →</router-link>
      </template>
      <p v-else class="hint">巡检员（只读）可查看温栏与温度清单，但不能登记或提交。</p>
      <p v-if="note" style="color:#0a6b2c">{{ note }}</p>
    </section>

    <!-- 已锁温度清单 -->
    <h3>已锁温度清单（温度随单冻结）</h3>
    <table border="1" cellpadding="6" style="border-collapse:collapse; width:100%;">
      <thead>
        <tr>
          <th>编号</th><th>灯种</th><th>环境温度(℃)</th><th>状态</th><th>结论</th><th>提交人</th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="j in jobs"
          :key="j.id"
          style="cursor:pointer"
          @click="goDetail(j.id)"
        >
          <td>{{ j.id }}</td>
          <td>{{ j.lamp }}</td>
          <td>{{ j.ambient_c }}</td>
          <td>{{ j.status }}</td>
          <td>{{ j.verdict }}</td>
          <td>{{ j.created_by }}</td>
        </tr>
      </tbody>
    </table>
  </div>
</template>

<style scoped>
.hint {
  color: #666;
  font-size: 13px;
}
</style>

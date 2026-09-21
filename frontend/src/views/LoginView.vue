<script setup>
import { nextTick, reactive, ref } from 'vue'
import { bootstrapAccount, loginAccount } from '../api/reports'
import heroImage from '../assets/home-laser-cutting.webp'
import AppIcon from '../components/AppIcon.vue'

const props = defineProps({
  setupRequired: Boolean,
  workerDatabaseAuthEnabled: Boolean,
})
const emit = defineEmits(['authenticated'])
const loading = ref(false)
const error = ref('')
const errorElement = ref(null)
const form = reactive({ username: '', display_name: '', password: '' })

async function submit() {
  loading.value = true
  error.value = ''
  try {
    let result
    if (props.setupRequired) {
      result = await bootstrapAccount(form)
    } else {
      result = await loginAccount({
        username: form.username,
        password: form.password,
      })
    }
    emit('authenticated', result.user)
  } catch (exception) {
    error.value = exception.message
    await nextTick()
    errorElement.value?.focus()
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <main class="login-page" :style="{ '--login-scene-image': `url(${heroImage})` }">
    <section class="login-scene" aria-label="系统介绍">
      <div class="login-scene__brand"><div class="login-brand"><span></span><span></span><span></span></div><strong>切割生产智能产报系统</strong></div>
      <div class="login-scene__copy">
        <p>PRODUCTION REPORTING</p>
        <p class="login-scene__title">数据驱动生产<br />让现场更清晰</p>
        <span>汇集生产报表、设备能耗、告警洞察与文件派发，形成统一的内网生产工作台。</span>
      </div>
      <ul>
        <li><AppIcon name="chart" :size="17" /><span><strong>生产报表</strong><small>统一统计与导出口径</small></span></li>
        <li><AppIcon name="bell" :size="17" /><span><strong>智能告警</strong><small>异常分析与实体报告</small></span></li>
        <li><AppIcon name="send" :size="17" /><span><strong>文件派发</strong><small>PDM 文件校验与推送</small></span></li>
      </ul>
    </section>
    <section class="login-card">
      <p class="eyebrow">WORKSHOP PRODUCTION</p>
      <h1>{{ setupRequired ? '初始化管理员' : '系统登录' }}</h1>
      <p>{{ setupRequired ? '首次使用需要创建一个本地管理员账号，完成后可从 HR 人员库继续授权管理员。' : workerDatabaseAuthEnabled ? '使用公司工号或管理员账号登录，系统会自动识别账号权限并进入对应页面。' : '使用管理员账号和密码登录。' }}</p>
      <form @submit.prevent="submit">
        <label><span>{{ !setupRequired && workerDatabaseAuthEnabled ? '公司工号 / 管理员账号' : '管理员账号' }}</span><input v-model.trim="form.username" name="username" :minlength="setupRequired ? 2 : 1" maxlength="64" autocomplete="username" spellcheck="false" required /></label>
        <label v-if="setupRequired"><span>显示姓名</span><input v-model.trim="form.display_name" name="display_name" maxlength="64" autocomplete="name" required /></label>
        <label><span>密码</span><input v-model="form.password" name="password" type="password" :minlength="setupRequired ? 8 : 1" maxlength="128" autocomplete="current-password" required /></label>
        <div v-if="error" ref="errorElement" class="login-error" role="alert" tabindex="-1">{{ error }}，请检查账号和密码后重试。</div>
        <button class="primary" type="submit" :disabled="loading">{{ loading ? '正在提交' : setupRequired ? '创建并登录' : '登录' }}</button>
      </form>
    </section>
  </main>
</template>

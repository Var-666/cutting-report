<script setup>
import GlobalSearch from './global-search/index.vue'
import LangSwitch from '../../components/common/lang-switch.vue'
import { useThemeStore } from '../../store/modules/theme'
import { getLocale, setLocale } from '../../locales'
import { localStg } from '../../utils/storage'

const theme = useThemeStore()
const options = [{ label: '中文', key: 'zh-CN' }, { label: 'English', key: 'en-US' }]
function changeLocale(locale) { setLocale(locale); localStg.set('lang', locale) }
</script>
<template>
  <GlobalSearch v-if="theme.header.globalSearch.visible" />
  <LangSwitch v-if="theme.header.multilingual.visible" :lang="getLocale()" :lang-options="options" @change-lang="changeLocale" />
</template>

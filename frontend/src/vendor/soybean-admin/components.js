import { h } from 'vue'
import { Icon } from '@iconify/vue/offline'
import { icons } from './icons.js'
import { NBadge, NButton, NCheckbox, NColorPicker, NDivider, NDrawer, NDrawerContent, NInput, NInputNumber, NMenu, NSelect, NSwitch, NTab, NTabs, NTooltip, NModal, NEmpty, NInputGroup, NScrollbar, NDropdown } from 'naive-ui'
import SvgIcon from '../../components/custom/svg-icon.vue'
import IconTooltip from '../../components/common/icon-tooltip.vue'
import DarkModeContainer from '../../components/common/dark-mode-container.vue'
import ButtonIcon from '../../components/custom/button-icon.vue'
import MenuToggler from '../../components/common/menu-toggler.vue'
import PinToggler from '../../components/common/pin-toggler.vue'
export function setupSoybeanComponents(app) {
  for (const [name, icon] of Object.entries({ 'icon-uil-search': 'search', 'icon-mdi-keyboard-return': 'return', 'icon-mdi-arrow-up-thin': 'up', 'icon-mdi-arrow-down-thin': 'down', 'icon-mdi-keyboard-esc': 'escape', 'icon-ant-design-enter-outlined': 'return' })) app.component(name, { render: () => h(Icon, { icon: icons[icon] }) })
  for (const [name, component] of Object.entries({ NBadge, NButton, NCheckbox, NColorPicker, NDivider, NDrawer, NDrawerContent, NInput, NInputNumber, NMenu, NSelect, NSwitch, NTab, NTabs, NTooltip, NModal, NEmpty, NInputGroup, NScrollbar, NDropdown, SvgIcon, IconTooltip, DarkModeContainer, ButtonIcon, MenuToggler, PinToggler })) app.component(name, component)
}

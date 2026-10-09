import type { CatalogDevice, DeviceState } from '../api/types';

export function visibleDevices(known: CatalogDevice[], observed: DeviceState[]): DeviceState[] {
  const physical = new Map<string, DeviceState>();
  for (const device of known) {
    const state = observed.find(
      (d) =>
        d.device_instance_id === device.device_instance_id ||
        (d.physical_resource_id === device.physical_resource_id &&
          d.component_id === device.component_id),
    );
    const resource = device.physical_resource_id ?? device.device_instance_id;
    const component = device.component_id ?? device.device_instance_id;
    physical.set(
      `${resource}:${component}`,
      state ?? {
        device_instance_id: device.device_instance_id,
        physical_resource_id: resource,
        component_id: component,
        availability_status: 'UNOBSERVED',
        occupancy_status: 'UNOBSERVED',
        active_execution_id: null,
      },
    );
  }
  return [...physical.values()];
}

export function deviceActions(
  device: DeviceState,
  executionId?: string | null,
): ('recover' | 'release')[] {
  const actions: ('recover' | 'release')[] = [];
  if (device.availability_status !== 'AVAILABLE') actions.push('recover');
  if (device.occupancy_status !== 'FREE' && executionId) actions.push('release');
  return actions;
}

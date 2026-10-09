// 明确合成的设备观察边界；不把静态目录当作实际空闲证据。
import { expect, it } from 'vitest';
import { visibleDevices } from '../../src/model/execution';
it('shows known unobserved devices without claiming that their chamber is free', () => {
  const list = visibleDevices(
    [
      {
        device_instance_id: 'toy-device',
        physical_resource_id: 'toy-resource',
        component_id: 'toy-chamber',
      },
    ],
    [],
  );
  expect(list).toHaveLength(1);
  expect(list[0]?.occupancy_status).toBe('UNOBSERVED');
  expect(list[0]?.availability_status).toBe('UNOBSERVED');
});
it('groups aliases by physical component and retains observed occupation', () => {
  const list = visibleDevices(
    [
      {
        device_instance_id: 'alias-a',
        physical_resource_id: 'toy-resource',
        component_id: 'toy-chamber',
      },
      {
        device_instance_id: 'alias-b',
        physical_resource_id: 'toy-resource',
        component_id: 'toy-chamber',
      },
    ],
    [
      {
        device_instance_id: 'alias-b',
        physical_resource_id: 'toy-resource',
        component_id: 'toy-chamber',
        occupancy_status: 'OCCUPIED',
        availability_status: 'AVAILABLE',
        active_execution_id: 'toy-execution',
      },
    ],
  );
  expect(list).toHaveLength(1);
  expect(list[0]?.active_execution_id).toBe('toy-execution');
});

from mesh_ir.gate3_oracle import read_facts_document


class ServingTimeline:
    def __init__(self, mode, plan=None):
        self.plan = plan or dict(initial_mode=mode, switches=[])
        self.switch_cursor = 0
        self.windows = [dict(mode=self.plan['initial_mode'], trigger_tick=0, start_tick=0,
                             drained=True, warmup_ticks=0, approximation_error=None)]

    def snapshot(self):
        return dict(switch_cursor=self.switch_cursor, windows=self.windows)

    def restore(self, state):
        cursor = state['switch_cursor']
        if not 0 <= cursor <= len(self.plan['switches']) or len(state['windows']) != cursor + 1:
            raise ValueError('E_CHECKPOINT_STATE: invalid timing window cursor')
        self.switch_cursor = cursor
        self.windows = state['windows']

    def run(self, simulate, cur_tick, frontend, limit, poll_ticks, save_at=None, save=None):
        while True:
            switch = (self.plan['switches'][self.switch_cursor]
                      if self.switch_cursor < len(self.plan['switches']) else None)
            trigger = min(switch['at_tick'] if switch else limit,
                          save_at if save_at is not None else limit)
            if trigger >= limit:
                return simulate(max(0, limit - cur_tick()))
            event = simulate(max(0, trigger - cur_tick()))
            if event.getCause() != 'simulate() limit reached':
                return event
            frontend.requestProgramBoundary()
            while not frontend.programBoundaryReady():
                event = simulate(min(poll_ticks, limit - cur_tick()))
                if event.getCause() != 'simulate() limit reached' or cur_tick() >= limit:
                    return event
            if switch and switch['at_tick'] == trigger:
                frontend.switchProgramTiming(switch['mode'] == 'FAST_EVENT')
                self.windows[-1]['end_tick'] = cur_tick()
                self.windows.append(dict(mode=switch['mode'], trigger_tick=trigger,
                    start_tick=cur_tick(), drained=True, warmup_ticks=0, approximation_error=None))
                self.switch_cursor += 1
            if save_at == trigger:
                save(cur_tick(), self.snapshot())
                save_at = None
            frontend.resumeProgramBoundary()

    def document(self, end_tick, facts_path):
        self.windows[-1]['end_tick'] = end_tick
        facts = read_facts_document(facts_path)
        for window in self.windows:
            window['aggregation'] = 'AXI_MESSAGE' if window['mode'] == 'FAST_EVENT' else 'FLIT'
            window['captured_request_ids'] = sorted({event['request_id'] for event in facts.events
                if event['kind'] == 'SQ_CONSUME' and
                window['start_tick'] <= event['tick'] < window['end_tick']})
        return dict(schema='timing_windows_v1', boundary='PROGRAM', windows=self.windows)

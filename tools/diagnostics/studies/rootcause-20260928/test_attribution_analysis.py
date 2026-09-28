import unittest
from analyze_vpp_attribution import match_envelope, partition

def stage(total,**children):
    return {'inclusive_us':total,'children':children,
            'unclassified_remainder_us':total-sum(c['inclusive_us'] for c in children.values())}

class EvidenceTests(unittest.TestCase):
    def test_unique_enclosure_and_no_cross_thread_match(self):
        events={42:[{'begin':1.,'end':1.02,'id':1}],43:[{'begin':1.,'end':1.03,'id':2}]}
        self.assertEqual(match_envelope(events,42,1.001,1.019)['id'],1)
        self.assertIsNone(match_envelope(events,42,1.001,1.021))
        self.assertEqual(match_envelope(events,43,1.001,1.021)['id'],2)
        self.assertIsNone(match_envelope(events,44,1.001,1.019))
    def test_nested_costs_partition_without_double_count(self):
        s=stage(13000,vpp_admission=stage(2000),
            descriptor_upload=stage(9500,cdma_call=stage(9400,direct_mutex_calls=stage(9000))),
            vpp_completion_wait=stage(1000))
        p=partition(s)
        self.assertEqual(p['cdma_mutex_us'],9000)
        self.assertEqual(p['cdma_other_us'],400)
        self.assertEqual(p['descriptor_other_us'],100)
        self.assertEqual(p['other_us'],500)
        self.assertEqual(sum(p.values()),13000)
    def test_inconsistent_nested_time_rejected(self):
        with self.assertRaises(ValueError):
            partition(stage(100,descriptor_upload=stage(200)))

if __name__=='__main__':unittest.main()

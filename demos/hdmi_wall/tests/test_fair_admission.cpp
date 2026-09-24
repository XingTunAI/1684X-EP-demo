#include "../../common/fair_admission.hpp"
#include <atomic>
#include <thread>
#include <vector>
#include <stdexcept>
int main() {
    FairAdmission limiter(2);
    std::atomic<int> active(0), total(0);
    std::atomic<bool> violation(false);
    std::vector<std::thread> threads;
    for(int i=0;i<8;++i) threads.emplace_back([&] {
        for(int j=0;j<100;++j) {
            AdmissionLease lease(limiter);
            if(++active>2) violation=true;
            std::this_thread::yield();
            ++total; --active;
        }
    });
    for(auto& t:threads) t.join();
    if(violation || total!=800 || active!=0) return 1;
    try { AdmissionLease lease(limiter); throw std::runtime_error("release on failure"); }
    catch(const std::runtime_error&) {}
    AdmissionLease a(limiter), b(limiter);
    return 0;
}

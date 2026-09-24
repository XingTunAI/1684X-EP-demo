#include "../local_catchup.hpp"
#include <iostream>
#include <stdexcept>
static void require(bool ok){if(!ok)throw std::runtime_error("catchup invariant failed");}
int main(){
    const std::vector<uint64_t> starts{0,50,100};
    auto a=local_catchup::plan(starts,125,25,20,1.1,.25);
    require(a.available&&a.sequence==50&&a.loop==0&&a.segment==1);
    auto b=local_catchup::plan(starts,125,25,120,4.9,.25);
    require(b.available&&b.sequence==175&&b.loop==1&&b.segment==1);
    auto c=local_catchup::plan(starts,125,25,50,1,.25);
    require(!c.available);
    auto d=local_catchup::plan(starts,125,25,10,2,0);
    require(d.sequence==50); // Exact boundary remains that keyframe.
    auto e=local_catchup::plan(starts,125,25,120,4.4,0);
    require(e.sequence==125&&e.segment==0&&e.loop==1);
    bool rejected=false;
    try{local_catchup::plan({0,50,50},125,25,0,1,.25);}catch(const std::invalid_argument&){rejected=true;}
    require(rejected);
    std::cout<<"PASS: source-clock catchup, keyframe selection, loop boundary, monotonicity and invalid index\n";
}

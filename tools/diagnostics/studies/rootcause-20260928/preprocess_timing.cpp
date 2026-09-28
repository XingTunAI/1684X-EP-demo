// Temporary LD_PRELOAD observer. Calls the original APIs unchanged; buffers
// records per thread and writes after that thread exits, outside steady work.
#include "bmcv_api_ext.h"
#include <dlfcn.h>
#include <time.h>
#include <sys/syscall.h>
#include <unistd.h>
#include <cstdlib>
#include <cstdio>
#include <vector>

namespace {
struct Event { double begin,end; int kind,iw,ih,ow,oh,ret; };
double stamp() { timespec t; clock_gettime(CLOCK_MONOTONIC,&t);return t.tv_sec+t.tv_nsec*1e-9; }
struct Events {
    std::vector<Event> values;
    long tid=syscall(SYS_gettid);
    unsigned lost=0;
    Events(){values.reserve(8192);}
    ~Events(){
        const char* dir=getenv("PREPROCESS_TRACE_DIR");
        if(!dir || values.empty())return;
        char path[4096];snprintf(path,sizeof(path),"%s/%ld.csv",dir,tid);
        FILE* f=fopen(path,"wx");
        if(!f){perror("preprocess trace open");return;}
        fprintf(f,"# tid=%ld lost=%u\nbegin,end,kind,iw,ih,ow,oh,ret\n",tid,lost);
        for(const auto&e:values)fprintf(f,"%.9f,%.9f,%d,%d,%d,%d,%d,%d\n",e.begin,e.end,e.kind,e.iw,e.ih,e.ow,e.oh,e.ret);
        fclose(f);
    }
};
thread_local unsigned depth=0;
Events& events(){static thread_local Events e;return e;}
struct Scope {
    bool top; Event e{};
    Scope(int kind,bm_image* input,bm_image* output):top(depth++==0){
        if(top){events();e.kind=kind;e.iw=input->width;e.ih=input->height;e.ow=output->width;e.oh=output->height;e.begin=stamp();}
    }
    void finish(int ret){
        if(top){e.end=stamp();e.ret=ret;auto& v=events();if(v.values.size()<200000)v.values.push_back(e);else ++v.lost;}
        --depth;
    }
};
template<class F> F original(const char* name){
    void* symbol=dlsym(RTLD_NEXT,name);
    if(!symbol){fprintf(stderr,"missing real symbol %s\n",name);abort();}
    return reinterpret_cast<F>(symbol);
}
}
extern "C" bm_status_t bmcv_image_vpp_basic(bm_handle_t h,int n,bm_image* in,bm_image* out,int* crops,bmcv_rect_t* rect,bmcv_padding_atrr_t* pad,bmcv_resize_algorithm alg,csc_type_t csc,csc_matrix_t* matrix){
    static auto real=original<decltype(&bmcv_image_vpp_basic)>("bmcv_image_vpp_basic");
    Scope scope(1,in,out);auto r=real(h,n,in,out,crops,rect,pad,alg,csc,matrix);scope.finish(r);return r;
}
extern "C" bm_status_t bmcv_image_convert_to(bm_handle_t h,int n,bmcv_convert_to_attr attr,bm_image* in,bm_image* out){
    static auto real=original<decltype(&bmcv_image_convert_to)>("bmcv_image_convert_to");
    Scope scope(2,in,out);auto r=real(h,n,attr,in,out);scope.finish(r);return r;
}

// Temporary ABI-preserving observer. No output during steady-state calls.
#include "bmcv_api_ext.h"
#include <dlfcn.h>
#include <time.h>
#include <sys/syscall.h>
#include <unistd.h>
#include <cstdlib>
#include <cstdio>
#include <cstring>
#include <cerrno>
#include <vector>

namespace {
struct Event { double begin=0,end=0; unsigned long id=0,parent=0; int kind=0,iw=0,ih=0,ow=0,oh=0,ret=0,batch=0; };
double stamp(){timespec t;clock_gettime(CLOCK_MONOTONIC,&t);return t.tv_sec+t.tv_nsec*1e-9;}
struct Events {
    std::vector<Event> values;
    long tid=syscall(SYS_gettid);
    unsigned lost=0; unsigned long next=0;
    Events(){values.reserve(16384);}
    ~Events(){
        const char* dir=getenv("PREPROCESS_TRACE_DIR");if(!dir||values.empty())return;
        char path[4096];snprintf(path,sizeof(path),"%s/%ld.csv",dir,tid);
        FILE* f=fopen(path,"wx");if(!f){perror("vpp observer output");return;}
        fprintf(f,"# tid=%ld lost=%u\nbegin,end,id,parent,kind,iw,ih,ow,oh,ret,batch\n",tid,lost);
        for(const auto&e:values)fprintf(f,"%.9f,%.9f,%lu,%lu,%d,%d,%d,%d,%d,%d,%d\n",e.begin,e.end,e.id,e.parent,e.kind,e.iw,e.ih,e.ow,e.oh,e.ret,e.batch);
        fclose(f);
    }
    void add(Event e){if(values.size()<400000)values.push_back(e);else ++lost;}
};
Events& events(){static thread_local Events e;return e;}
thread_local unsigned depth=0;
thread_local unsigned long parent=0;
template<class F>F original(const char* name){void*p=dlsym(RTLD_NEXT,name);if(!p){fprintf(stderr,"missing %s\n",name);abort();}return reinterpret_cast<F>(p);}
struct Scope {
    bool top;Event e;
    Scope(int kind,bm_image* in,bm_image* out):top(depth++==0){
        if(top){auto&v=events();e.id=++v.next;parent=e.id;e.kind=kind;e.iw=in->width;e.ih=in->height;e.ow=out->width;e.oh=out->height;e.begin=stamp();}
    }
    void finish(int r){if(top){int saved_errno=errno;e.end=stamp();e.ret=r;events().add(e);parent=0;errno=saved_errno;}--depth;}
};
}
extern "C" bm_status_t bmcv_image_vpp_basic(bm_handle_t h,int n,bm_image* in,bm_image* out,int* crops,bmcv_rect_t* rect,bmcv_padding_atrr_t* pad,bmcv_resize_algorithm alg,csc_type_t csc,csc_matrix_t* matrix){
    static auto real=original<decltype(&bmcv_image_vpp_basic)>("bmcv_image_vpp_basic");
    Scope scope(1,in,out);auto r=real(h,n,in,out,crops,rect,pad,alg,csc,matrix);scope.finish(r);return r;
}
extern "C" bm_status_t bmcv_image_convert_to(bm_handle_t h,int n,bmcv_convert_to_attr attr,bm_image* in,bm_image* out){
    static auto real=original<decltype(&bmcv_image_convert_to)>("bmcv_image_convert_to");
    Scope scope(2,in,out);auto r=real(h,n,attr,in,out);scope.finish(r);return r;
}
// Installed bm_trigger_vpp passes this pointer unchanged to BMDEV_TRIGGER_VPP.
// The paired driver ABI starts vpp_batch_n with int num, then descriptor* cmd.
extern "C" bm_status_t bm_trigger_vpp(bm_handle_t h,void* batch){
    using F=bm_status_t(*)(bm_handle_t,void*);static auto real=original<F>("bm_trigger_vpp");
    auto&v=events();Event e;e.id=++v.next;e.parent=parent;e.kind=3;
    if(batch)std::memcpy(&e.batch,batch,sizeof(e.batch));
    e.begin=stamp();auto r=real(h,batch);int saved_errno=errno;e.end=stamp();e.ret=r;v.add(e);errno=saved_errno;return r;
}
